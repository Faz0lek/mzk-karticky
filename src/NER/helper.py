import torch
import numpy as np
import typing
import json
import os

from sklearn.metrics import accuracy_score

from transformers import BertTokenizerFast

JOKER = chr(65533)
LINE_SEPARATOR = "[LF]"
BERT_BASE_NAME = "bert-base-multilingual-uncased"

UNKNOWN_CHARS = {
        "—": "-",
        "ϵ": JOKER,
        "℥": JOKER,
        "‘": JOKER,
        "’": JOKER,
        "`": JOKER,
        "“": '"',
        "☞": JOKER,
        "☜": JOKER,
        "˛": ".",
        "⁂": JOKER,
        "ꝛ": JOKER,
        "Ꙃ": "z",
        "Ꙁ": "z",
        "Ꙋ": JOKER,
        "Ѡ": JOKER,
        "Ꙗ": JOKER,
        "Ѥ": JOKER,
        "Ѭ": JOKER,
        "Ѩ": JOKER,
        "Ѯ": JOKER,
        "Ѱ": JOKER,
        "Ѵ": "v",
        "Ҁ": "c",
        "ꙃ": "z",
        "ꙁ": "z",
        "ꙋ": JOKER,
        "ѡ": "w",
        "ꙗ": JOKER,
        "ѥ": JOKER,
        "ѭ": JOKER,
        "ѩ": JOKER,
        "ѯ": JOKER,
        "ѱ": JOKER,
        "ѵ": "v",
        "ҁ": "c",
        "Ӕ": JOKER,
        "ӕ": JOKER,
        "Ϲ": "c",
        "ϲ": "c",
        "ϳ": "j",
        "ϝ": "f",
        "Ⱥ": "a",
        "ⱥ": "a",
        "Ɇ": "e",
        "ɇ": "e",
        "ᵱ": "p",
        "ꝓ": "p",
        "ꝑ": "p",
        "ꝙ": "q",
        "ꝗ": "q",
        "ꝟ": "v",
}

TRUNCATED_CHARS = ["ͤ", "̄", "̾", "̃", "̊"]


def load_ocr(path: str, txn = None, raw: bool = False) -> str:
    # Load text
    if txn is not None:
        text = txn.get(path.encode()).decode()
    else:
        with open(path, 'r') as f:
            text = f.read()

    # Replace unknown chars so model does not generate [UNK] tokens
    for to_replace, replace_with in UNKNOWN_CHARS.items():
        text = text.replace(to_replace, replace_with)

    # Remove special accent characters that get discarded by the tokenizer
    for to_remove in TRUNCATED_CHARS:
        text = text.replace(to_remove, "")

    return text


def add_line_separator_token(text: str) -> str:
    return text.replace("\n", f" {LINE_SEPARATOR} ")


class ModelConfig:
    ALL_LABELS = ["Author", "Title", "Original_title", "Publisher", "Pages", "Series", "Edition", "References", "ID", "ISBN", "ISSN", "Topic", "Subtitle", "Date", "Institute", "Volume"]
    FILENAME = r"model_config.json"

    def __init__(
        self,
        labels: str = "all",
        format: str = "iob",
        max_len: int = 256,
        sep: bool = True,
        sep_loss: bool = False,
        must_align: list = [],
        min_aligned: int = 1,
    ):
        self.labels = self.get_labels(labels)
        self.format = self.get_format(format)
        self.num_labels = len(self.labels) * len([tag for tag in self.format if tag != "O"]) + 1

        self.labels2ids = self.get_labels2ids(format)
        self.labels2ids["O"] = 0
        self.ids2labels = {v: k for k, v in self.labels2ids.items()}

        self.max_sequence_len = max_len

        self.sep = sep
        self.sep_loss = sep_loss

        self.must_align = must_align
        self.min_aligned = min_aligned

        if self.min_aligned > len(self.labels):
            raise ValueError("The value of min_aligned must be lower than the number of labels.")

        if self.min_aligned < 1:
            raise ValueError("The value of min_aligned must be higher than zero.")

        if not set(self.must_align).issubset(set(self.labels)):
            raise ValueError("The values of must_align must contain correct label names")

    def save(self, path: str):
        path = os.path.join(path, self.FILENAME)

        json_obj = json.dumps(self.__dict__, indent=4)

        with open(path, "w") as f:
            f.write(json_obj)

    @classmethod
    def load(cls, path: str):
        path = os.path.join(path, cls.FILENAME)
        
        with open(path, "r") as f:
            data = json.load(f)

        config = ModelConfig()

        for key, val in data.items():
            setattr(config, key, val)

        # Change data type of ids2labels keys to int
        config.ids2labels = {int(v): k for v, k in config.ids2labels.items()}

        return config

    def get_format(self, format: str):
        return list(format.upper())

    def get_labels(self, label_str: str):
        if label_str == "all":
            return self.ALL_LABELS

        if label_str == "subset":
            return ["Author", "Title", "ID", "Pages", "Volume", "Publisher", "Edition", "Date"]

    def get_labels2ids(self, format_str: str):
        if format_str == "iob":
            no_o = ["B", "I"]
            return {f"{c}-{label}": i * len(no_o) + j + 1 for i, label in enumerate(self.labels) for j, c in enumerate(no_o)}
        
        if format_str == "io":
            return {label: i + 1 for i, label in enumerate(self.labels)}

    def __str__(self):
        output = f"Labels used: {self.labels}\n\n"

        output += f"Number of labels: {self.num_labels}\n"
        output += f"Format used: {self.format}\n\n"

        output += f"labels2ids: {self.labels2ids}\n\n"
        output += f"ids2labels: {self.ids2labels}\n\n"

        output += f"Max seq length: {self.max_sequence_len}\n"

        output += f"Separating: {self.sep}\n"
        output += f"Loss on sep: {self.sep_loss}\n\n"

        output += f"Min aligned fields: {self.min_aligned}\n"
        output += f"Must aligned fields: {self.must_align}"
        
        return output


def calculate_acc(labels, logits, num_labels):
    flattened_targets = labels.view(-1)  # shape (batch_size * seq_len,)
    active_logits = logits.view(-1, num_labels)  # shape (batch_size * seq_len, num_labels)
    flattened_predictions = torch.argmax(active_logits, axis=1).to(flattened_targets.device)  # shape (batch_size * seq_len,)
    active_accuracy = labels.view(-1) != -100  # shape (batch_size, seq_len)

    labels_acc = torch.masked_select(flattened_targets, active_accuracy)
    predictions_acc = torch.masked_select(flattened_predictions, active_accuracy)

    return accuracy_score(labels_acc.cpu().numpy(), predictions_acc.cpu().numpy()), labels_acc, predictions_acc


def calculate_confidence(logits):
    return torch.nn.functional.softmax(logits, dim=2).cpu().numpy().max(axis=2).flatten()


def build_tokenizer(path: str, model_config: ModelConfig=ModelConfig()):
    if path == BERT_BASE_NAME:
        tokenizer = BertTokenizerFast.from_pretrained(BERT_BASE_NAME)
        tokenizer.add_special_tokens({"additional_special_tokens": [JOKER]})

        if model_config.sep:
            tokenizer.add_special_tokens({"additional_special_tokens": [LINE_SEPARATOR]})
            
        return tokenizer

    return BertTokenizerFast.from_pretrained(path)


def offsets_to_io(text: str, alignments, sep: bool = False):
    text_c = text

    # We have to replace newlines with something that cannot be present in the text.
    # The truncated chars are never present as they are removed in preprocessing.
    # We do this so the text is not split on newlines in case of line separations
    # with a special token.
    if sep:
        newline_joker = TRUNCATED_CHARS[0]
        text_c = text.replace("\n", newline_joker)

    tokens = text_c[:alignments[0].start].split()
    labels = ["O"] * len(tokens)

    for i, alignment in enumerate(alignments):
        tokens += text_c[alignment.start:alignment.end].split()
        labels += [alignment.label] * (len(tokens) - len(labels))

        try:
            next_alignment = alignments[i + 1]
            tokens += text_c[alignment.end:next_alignment.start].split()
        except IndexError:
            tokens += text_c[alignment.end:].split()

        labels += ["O"] * (len(tokens) - len(labels))

    # IF SEP
    # We have to do a backward correction: replace all newline jokers with newlines and split all tokens again
    if sep:
        out_tokens = []
        out_labels = []

        for token, label in zip(tokens, labels):
            if newline_joker not in token:
                out_tokens.append(token)
                out_labels.append(label)
                continue

            split_token = token.replace(newline_joker, f" {LINE_SEPARATOR} ").split()
            out_tokens.extend(split_token)
            out_labels.extend([label] * len(split_token))

        return out_tokens, out_labels

    return tokens, labels


def offsets_to_iob(text: str, alignments, sep: bool = False):
    tokens, labels = offsets_to_io(text, alignments, sep)

    current = ""
    for i, (token, label) in enumerate(zip(tokens, labels)):
        if label == "O":
            current = "O"
            continue

        tmp = label
        labels[i] = f"B-{label}" if label != current else f"I-{label}"
        current = tmp

    return tokens, labels
