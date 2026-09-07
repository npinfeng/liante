"""Application boundary between APIs, task execution, and MLP AutoML."""

from __future__ import annotations

from automl.mlp_trainer import MLPAutoMLTrainer
from schemas.training import TrainAllRequest, TrainRequest
from services.prediction_service import PredictionService
from tasks.task_manager import TrainingTaskManager


class TrainingService:
    def __init__(
        self,
        trainer: MLPAutoMLTrainer,
        task_manager: TrainingTaskManager,
        prediction_service: PredictionService,
    ) -> None:
        self.trainer = trainer
        self.task_manager = task_manager
        self.prediction_service = prediction_service

    def queue_channel(self, request: TrainRequest) -> str:
        def run(update):
            result = self.trainer.train_channel(
                request.channel,
                n_trials=request.n_trials,
                max_epochs=request.max_epochs,
                patience=request.patience,
                random_seed=request.random_seed,
                search_space=request.search_space,
                status_callback=update,
            )
            self.prediction_service.invalidate(request.channel)
            return result

        return self.task_manager.submit(request.channel, request.n_trials, run)

    def queue_all(self, request: TrainAllRequest) -> str:
        total_trials = request.n_trials * len(request.channels)

        def run(update):
            results = []
            for channel_index, channel in enumerate(request.channels):
                trial_offset = channel_index * request.n_trials

                def update_channel(**values):
                    current = int(values.get("current_trial", 0))
                    values["channel"] = channel
                    values["current_trial"] = trial_offset + current
                    values["total_trials"] = total_trials
                    local_progress = float(values.get("progress", 0)) / 100
                    values["progress"] = int(
                        (channel_index + local_progress) * 100 / len(request.channels)
                    )
                    update(**values)

                result = self.trainer.train_channel(
                    channel,
                    n_trials=request.n_trials,
                    max_epochs=request.max_epochs,
                    patience=request.patience,
                    random_seed=request.random_seed + channel,
                    search_space=request.search_space,
                    status_callback=update_channel,
                )
                self.prediction_service.invalidate(channel)
                results.append(result)
            return {"channels": results, "trained_channels": request.channels}

        return self.task_manager.submit(request.channels[0], total_trials, run)

