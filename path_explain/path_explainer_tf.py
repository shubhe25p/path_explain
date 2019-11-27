"""
Contains a single import: the PathExplainerTF object, which is
used to get feature-level importances of TensorFlow
gradient-based models.
"""

import tensorflow as tf
import numpy as np
from tqdm import tqdm
from .explainer import Explainer

class PathExplainerTF(Explainer):
    """
    Explains a model using path attributions from the given baseline.
    """

    def __init__(self, model):
        """
        Initialize the TF explainer class. This class
        will handle both the eager and the
        old-school graph-based tensorflow models.

        Args:
            model: A tf.keras.Model instance if executing eagerly.
                   A tuple (input_tensor, output_tensor) otherwise.
        """
        self.model = model
        self.eager_mode = False
        try:
            self.eager_mode = tf.executing_eagerly()
        except AttributeError:
            pass

    def _sample_baseline(self, baseline, number_to_draw, use_expectation):
        """
        An internal function to sample the baseline.

        Args:
            baseline: The baseline tensor
            number_to_draw: The number of samples to draw
            use_expectation: Whether or not to sample baselines
                             or use a single baseline

        Returns:
            A tensor of shape (number_to_draw, ...) representing
            the baseline and a tensor of shape (number_to_draw) representing
            the sampled interpolation constants
        """
        if use_expectation:
            replace = baseline.shape[0] > number_to_draw
            sample_indices = np.random.choice(baseline.shape[0],
                                              size=number_to_draw,
                                              replace=replace)
            sampled_baseline = baseline[sample_indices]
        else:
            reps = np.ones(len(baseline.shape))
            reps[0] = number_to_draw
            sampled_baseline = np.tile(baseline, reps)
        return sampled_baseline

    def _sample_alphas(self, num_samples, use_expectation):
        """
        An internal function to sample the interpolation constant.

        Args:
            num_samples: Number of alphas to draw
            use_expectation: Whether or not to use
                expected gradients-style sampling
        """
        if use_expectation:
            return np.random.uniform(low=0.0, high=0.0, size=num_samples)
        else:
            return np.linspace(start=0.0, stop=1.0, num=num_samples, endpoint=True)

    def _single_attribution(self, current_input, current_baseline,
                            current_alphas, num_samples, batch_size,
                            use_expectation, output_index):
        """
        A helper function to compute path
        attributions for a single sample.

        Args:
            current_input: A single sample. Assumes that
                           it is of shape (...) where ...
                           represents the input dimensionality
            baseline: A tensor representing the baseline input.
            current_alphas: Which alphas to use when interpolating
            num_samples: The number of samples to draw
            batch_size: Batch size to input to the model
            use_expectation: Whether or not to sample the baseline
            output_index: Whether or not to index into a given class
        """
        current_input = np.expand_dims(current_input, axis=0)

        attribution_array = []
        for j in range(0, num_samples, batch_size):
            number_to_draw = min(batch_size, num_samples - j)

            batch_baseline = self._sample_baseline(current_baseline,
                                                   number_to_draw,
                                                   use_expectation)
            batch_alphas = current_alphas[j:min(j + batch_size, num_samples)]

            reps = np.ones(len(current_input.shape))
            reps[0] = number_to_draw
            batch_input = np.tile(current_input, reps)

            with tf.GradientTape() as tape:
                tape.watch(batch_input)

                batch_difference = batch_input - batch_baseline
                batch_interpolated = batch_alphas * batch_input + \
                                     (1.0 - batch_alphas) * batch_baseline
                batch_predictions = self.model(batch_interpolated)
                if output_index is not None:
                    batch_predictions = batch_predictions[:, output_index]

            batch_gradients = tape.gradient(batch_predictions, batch_input)
            batch_attributions = batch_gradients * batch_difference
            attribution_array.append(batch_attributions)
        attribution_array = np.concatenate(attribution_array, axis=0)
        attributions = np.mean(attribution_array, axis=0)
        return attributions

    def attributions(self, inputs, baseline,
                     batch_size=50, num_samples=100,
                     use_expectation=True, output_indices=None,
                     verbose=False):
        """
        A function to compute path attributions on the given
        inputs.

        Args:
            inputs: A tensor of inputs to the model of shape (batch_size, ...).
            baseline: A tensor of inputs to the model of shape
                      (num_refs, ...) where ... indicates the dimensionality
                      of the input.
            batch_size: The maximum number of inputs to input into the model
                        simultaneously.
            num_samples: The number of samples to use when computing the
                         expectation or integral.
            use_expectation: If True, this samples baselines and interpolation
                             constants uniformly at random (expected gradients).
                             If False, then this assumes num_refs=1 in which
                             case it uses the same baseline for all inputs,
                             or num_refs=batch_size, in which case it uses
                             baseline[i] for inputs[i] and takes 100 linearly spaced
                             points between baseline and input (integrated gradients).
            output_indices:  If this is None, then this function returns the
                             attributions for each output class. This is rarely
                             what you want for classification tasks. Pass an
                             integer tensor of shape [batch_size] to
                             index the output output_indices[i] for
                             the input inputs[i].
        """
        test_output = self.model(inputs[0:1])
        is_multi_output = len(test_output.shape) > 1

        if is_multi_output and output_indices is None:
            num_classes = test_output.shape[-1]
            attributions = np.zeros((num_classes,) + inputs.shape)
        elif not is_multi_output and output_indices is not None:
            raise ValueError('Provided output_indices but ' + \
                             'model is not multi output!')
        else:
            attributions = np.zeros(*inputs.shape)

        input_iterable = enumerate(inputs)
        if verbose:
            input_iterable = enumerate(tqdm(inputs))

        for i, current_input in input_iterable:
            current_alphas = self._sample_alphas(num_samples, use_expectation)

            if not use_expectation and baseline.shape[0] > 1:
                current_baseline = np.expand_dims(baseline[i], axis=0)
            else:
                current_baseline = baseline

            if is_multi_output:
                if output_indices is not None:
                    output_index = output_indices[i]
                    current_attributions = self._single_attribution(current_input,
                                                                    current_baseline,
                                                                    current_alphas,
                                                                    num_samples,
                                                                    batch_size,
                                                                    use_expectation,
                                                                    output_index)
                    attributions[i] = current_attributions
                else:
                    for output_index in range(num_classes):
                        current_attributions = self._single_attribution(current_input,
                                                                        current_baseline,
                                                                        current_alphas,
                                                                        num_samples,
                                                                        batch_size,
                                                                        use_expectation,
                                                                        output_index)
                        attributions[output_index, i] = current_attributions
            else:
                current_attributions = self._single_attribution(current_input,
                                                                current_baseline,
                                                                current_alphas,
                                                                num_samples,
                                                                batch_size,
                                                                use_expectation,
                                                                None)
                attributions[i] = current_attributions
        return attributions