import torch

from helper import calculate_acc
from sklearn.metrics import classification_report, ConfusionMatrixDisplay, confusion_matrix
import matplotlib.pyplot as plt


class Tester:
    def __init__(self, model):
        self.model = model

        self.device = self.model.get_device()

    def step(self, batch):
        with torch.no_grad():
            loss, logits = self.forward(batch)

        return loss.item(), logits.detach()

    def forward(self, batch):
        input_ids = batch["input_ids"].to(self.device)
        attention_mask = batch["attention_mask"].to(self.device)
        labels = batch["labels"].to(self.device)
        bboxes = batch["bboxes"].to(self.device) if self.model.config.backend == "lambert" or self.model.config.bboxes else None

        loss, logits = self.model(input_ids=input_ids, attention_mask=attention_mask, labels=labels, bboxes=bboxes)

        return loss, logits[0]

    def remove_format(self, labels):
        if self.model.config.format == ["I", "O", "B"]:
            return [label if label == "O" else label[2:] for label in labels]

        if self.model.config.format == ["I", "O"]:
            return labels

    def test(self, data_loader):
        self.model.eval()

        total_loss = 0
        total_acc = 0
        total_steps = 0

        truth = []
        prediction = []

        for batch in data_loader:
            loss, logits = self.step(batch)

            total_loss += loss
            acc, l, p = calculate_acc(batch["labels"], logits, self.model.num_labels)

            total_acc += acc
            truth.extend([self.model.config.ids2labels[id.item()] for id in l])
            prediction.extend([self.model.config.ids2labels[id.item()] for id in p])

            total_steps += 1

        total_loss /= total_steps
        total_acc /= total_steps

        # We don't really care about the tagging format when concerning total accuracy.
        truth = self.remove_format(truth)
        prediction = self.remove_format(prediction)

        print(f"Test loss: {total_loss:.6f}")
        print(f"Test acc: {total_acc:.6f}\n")
        print(classification_report(truth, prediction, zero_division=0))

        cm = confusion_matrix(y_true=truth, y_pred=prediction, labels=self.model.config.labels + ["O"])

        # false_positives = {l: sum(cm[:, i]) - cm[i, i] for i, l in enumerate(self.model.config.labels + ["O"])} # Columns
        # false_negatives = {l: sum(cm[i, :], 2) - cm[i, i] for i, l in enumerate(self.model.config.labels + ["O"])} # Rows

        # precision = {l: cm[i, i] / (cm[i, i] + false_positives[l]) for i, l in enumerate(self.model.config.labels + ["O"])}
        # recall = {l: cm[i, i] / (cm[i, i] + false_negatives[l]) for i, l in enumerate(self.model.config.labels + ["O"])}

        # for (l, p), r in zip(precision.items(), recall.values()):
        #     print(f"{l}:\tP = {p}\tR = {r}")

        # print(f"Avg precision = {sum(precision.values()) / len(precision.values())}")
        # print(f"Avg recall = {sum(recall.values()) / len(recall.values())}")
        ConfusionMatrixDisplay.from_predictions(truth, prediction)
        plt.savefig("confusion.pdf")
