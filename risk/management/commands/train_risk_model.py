from django.core.management.base import BaseCommand

from risk import training
from risk.model import MODEL_PATH


class Command(BaseCommand):
    help = "Train the dropout-risk model and write risk/model.json (needs scikit-learn)."

    def add_arguments(self, parser):
        parser.add_argument("--threshold", type=float, default=0.4)

    def handle(self, *args, threshold, **opts):
        model = training.train(threshold=threshold)
        training.write(model, MODEL_PATH)
        self.stdout.write(self.style.SUCCESS(f"Wrote {MODEL_PATH.name}: {model['metrics']}"))
