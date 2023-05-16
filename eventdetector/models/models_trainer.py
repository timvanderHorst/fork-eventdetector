import os
from typing import Dict, Tuple

import numpy as np
import tensorflow as tf
from keras.losses import mse
from numpy import ndarray
from sklearn.model_selection import KFold, train_test_split

from eventdetector import MODELS_DIR
from eventdetector.metamodel.utils import DataSplitter
from eventdetector.models import logger_models
from eventdetector.models.helpers import CustomEarlyStopping, custom_cross_val_score
from eventdetector.models.models_builder import ModelBuilder


class ModelTrainer:
    """
    A class used to train and evaluate machine learning models.

    Attributes:
        data_splitter (DataSplitter): An object of the DataSplitter class, which is used to split the data
            into train and test sets.
        epochs (int): The number of epochs to train the models.
        batch_size (int): The batch size to use during training.
        pa (int): The patience value to use for the EarlyStopping callback.
        t_r (float): The ratio value to use for the CustomEarlyStopping callback.
        use_kfold (bool): Whether to use K-Fold cross-validation or not.
        val_size (float): The size of the validation set to use during training.
        epsilon (float): A small constant used to control the size of set which contains the top models
            with the lowest MSE values.
        train_losses (Dict[str, list]): A dictionary containing the training losses for each model.
        val_losses (Dict[str, list]): A dictionary containing the validation losses for each model.
    """

    def __init__(self, data_splitter: DataSplitter, epochs: int,
                 batch_size: int, pa: int, t_r: float,
                 use_kfold: bool, val_size: float, epsilon: float) -> None:
        """
        Initialize the ModelTrainer object.

        Args:
            data_splitter (DataSplitter): An object of the DataSplitter class, which is used to split the data
                into train and test sets.
            epochs (int): The number of epochs to train the models.
            batch_size (int): The batch size to use during training.
            pa (int): The patience value to use for the EarlyStopping callback.
            t_r (float): The ratio value to use for the CustomEarlyStopping callback.
            use_kfold (bool): Whether to use K-Fold cross-validation or not.
            val_size (float): The size of the validation set to use during training.
            epsilon (float): A small constant used to control the size of set which contains the top models
                    with the lowest MSE values.
        """

        self.best_models: Dict[str, tf.keras.Model] = {}
        self.train_losses = {}
        self.val_losses = {}
        self.data_splitter = data_splitter
        self.epochs = epochs
        self.batch_size = batch_size
        self.pa = pa
        self.t_r = t_r
        self.use_kfold = use_kfold
        self.val_size = val_size
        self.epsilon = epsilon

    def fitting_models(self, created_models: Dict[str, tf.keras.Model]) -> None:
        """
        Fits the created models to the training data and saves the training and validation losses.

        Args:
            created_models: A dictionary containing the created models with their names as keys
                and the models as values.

        Returns:
            None
        """
        # Define early stopping based on validation loss
        early_stopping = tf.keras.callbacks.EarlyStopping(monitor='val_loss', patience=self.pa * 2)
        # Define custom early stopping based on a ratio and patience
        custom_early_stopping = CustomEarlyStopping(ratio=self.t_r, patience=self.pa, verbose=1)
        # Loop through each model in the created models dictionary
        for model_name, model in created_models.items():
            # If using k-fold cross-validation
            if self.use_kfold:
                logger_models.info("Performing a KFold cross-validation")
                # Calculate cross validation score using custom function
                score: float = custom_cross_val_score(model=model, x=self.data_splitter.train_x,
                                                      y=self.data_splitter.train_y,
                                                      cv=KFold(n_splits=5, shuffle=False), epochs=self.epochs,
                                                      batch_size=self.batch_size,
                                                      callbacks=[early_stopping, custom_early_stopping])
                # Print cross validation score for the current model
                logger_models.info(f"The cross validation score for {model_name} is {score}")
            # Split training data into training and validation sets
            train_x, val_x, train_y, val_y = train_test_split(self.data_splitter.train_x, self.data_splitter.train_y,
                                                              test_size=self.val_size,
                                                              shuffle=False)
            # Print message indicating fitting of a current model
            logger_models.info(f"Fitting of {model_name}...")
            # Fit the model using training data and validate using validation data
            history = model.fit(train_x, train_y, epochs=self.epochs,
                                batch_size=self.batch_size, verbose=1,
                                validation_data=(val_x, val_y),
                                callbacks=[early_stopping, custom_early_stopping])
            # Save training and validation errors for the current model
            self.train_losses[model_name] = history.history['loss']
            self.val_losses[model_name] = history.history['val_loss']

        losses_test_data: Dict[str, tf.keras.Model] = {}
        min_loss = np.Inf
        for model_name, model in created_models.items():
            logger_models.info(f"Evaluating model {model_name} on test data")
            loss = model.evaluate(self.data_splitter.test_x, self.data_splitter.test_y, batch_size=self.batch_size,
                                  use_multiprocessing=True)
            logger_models.info(f"The loss value of model {model_name} on test data is {loss:.4f}")
            losses_test_data[model_name] = loss
            if min_loss > loss:
                min_loss = loss

        logger_models.info(f"Selecting best models based on the min MSE {min_loss:.4f} and epsilon {self.epsilon}:")
        for model_name, loss_ in losses_test_data.items():
            if loss_ <= (min_loss + self.epsilon):
                self.best_models[model_name] = created_models[model_name]
        logger_models.info(f"Best models selected: {self.best_models.keys()}")

    def save_best_models(self, output_dir: str) -> None:
        """
        Save the best models to the specified output directory.

        Args:
             output_dir (str): The directory to save the best models.

        Returns:
            None
        """

        for model_name, model in self.best_models.items():
            # Print the name of the current model being saved
            logger_models.info(f"Current model to be saved on the disk is {model_name}")
            path = os.path.join(output_dir, MODELS_DIR)
            # Save the model to the specified directory
            model_path = os.path.join(path, model_name)
            model.save(model_path)
        logger_models.info("Models saved successfully.")

    def train_meta_model(self, type_training: str, hyperparams_ffn: Tuple[int, int, int], output_dir: str) \
            -> tuple[ndarray, float, ndarray]:
        """
        Trains the metamodel using the best models from each machine learning algorithm.

         Args:
            type_training: The type of training to use, either "ffn" or "mean".
            hyperparams_ffn: A tuple containing the hyperparameters for the feedforward neural network (FFN).
            output_dir: The directory to save the trained models to.

        Returns:
            A tuple containing the final prediction and the mean-squared error.
        """
        predictions = []
        for model_name, model in self.best_models.items():
            # Make predictions for the test set using each model
            predicted_y: np.ndarray = model.predict(self.data_splitter.test_x, batch_size=self.batch_size,
                                                    use_multiprocessing=True)
            predicted_y = predicted_y.flatten()
            predictions.append(predicted_y)

        # Convert a list of 1D NumPy arrays to 2D NumPy array
        x = np.stack(predictions, axis=1)

        if type_training == "ffn":
            logger_models.info("Train the MetaModel using a FFN to produce a final prediction")
            # Split the data into training and test sets
            train_x, test_x, train_y, test_y = train_test_split(x, self.data_splitter.test_y,
                                                                test_size=self.data_splitter.test_size,
                                                                shuffle=False)
            # Build the FFN model
            inputs = tf.keras.Input(shape=(train_x.shape[1],), name="Input")
            max_layers, min_units, max_units = hyperparams_ffn
            model_builder: ModelBuilder = ModelBuilder(inputs=inputs)
            name: str = "meta_model_ffn"
            units = [np.random.randint(min_units, max_units + 1) for _ in range(max_layers)]
            units = sorted(units, reverse=True)
            for j in range(max_layers):
                units_j = units[j]
                model_builder.add_dense_layer(units=units_j)
            model_builder.add_dense_layer(units=1, dropout=None)
            keras_model = model_builder.build(name=name, root_dir=output_dir)
            # Train the model
            logger_models.info("Fitting the MetaModel...")
            keras_model.fit(train_x, train_y, epochs=self.epochs, batch_size=self.batch_size, verbose=1,
                            validation_data=(test_x, test_y))

            path = os.path.join(output_dir, MODELS_DIR)
            model_path = os.path.join(path, name)
            keras_model.save(model_path)
            logger_models.info("MetaModel saved successfully.")

            # final_prediction: np.ndarray = keras_model.predict(self.data_splitter.test_x, batch_size=self.batch_size,
            #                                                   use_multiprocessing=True)
            final_prediction: np.ndarray = keras_model.predict(test_x, batch_size=self.batch_size,
                                                               use_multiprocessing=True)
            final_prediction = final_prediction.flatten()
            return final_prediction, mse(final_prediction, test_y), test_y
            # return final_prediction, mse(final_prediction, self.data_splitter.test_y), self.data_splitter.test_y
        else:
            # Compute the average prediction
            logger_models.info("Compute the average of predictions to produce a final prediction")
            final_prediction = np.mean(x, axis=1)
            return final_prediction, mse(final_prediction, self.data_splitter.test_y), self.data_splitter.test_y