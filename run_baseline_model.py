import string
import numpy as np
from baselines.models import RNNSL
from baselines.spacy_tagging import read_datafile

from evaluation.semeval2021 import f1  ## WRONG F1, ONLY USED FOR OFFSETS
from sklearn.metrics import f1_score
from evaluation.fix_spans import _contiguous_ranges
from keras.utils import to_categorical


def check_for_mismatch(tokens, texts, offset_mapping):
    for example in range(len(tokens)):

        tokenized_text = tokens[example]
        revived_text = [
            texts[example][
                offset_mapping[example][token][0] : offset_mapping[example][token][1]
            ]
            .lower()
            .translate(str.maketrans("", "", string.punctuation))
            for token in range(len(tokens[example]))
        ]
        if tokenized_text != revived_text:
            print(tokenized_text)
            print(revived_text)
            exit()


def is_whitespace(c):  ##From google-research/bert run_squad.py
    if (
        c == " "
        or c == "\t"
        or c == "\r"
        or c == "\n"
        or ord(c) == 0x202F
        or c in string.whitespace
        or ord(c) == 160
        or ord(c) == 8196
    ):
        return True
    return False


def convert_spans_to_token_labels(text, spans=None, test=False):
    token_labels = []
    token_to_offsets_map = []
    i = 0

    new_text = ""
    for c in text:
        if is_whitespace(c):
            new_text += " "
        else:
            new_text += c
    text = new_text
    while i < len(text):
        if is_whitespace(text[i]):
            i += 1
            continue
        else:
            # print(i,text[i])
            token_to_offsets_map.append(
                [
                    i,
                ]
            )
            if not test:
                if i in spans:
                    token_labels.append(2)  ##Toxic
                else:
                    token_labels.append(1)  ##Non-Toxic
            while i < len(text) and not is_whitespace(text[i]):
                i += 1
            token_to_offsets_map[-1].append(i)  ##Not Inclusive
    if not test:
        assert len(text.split()) == len(token_labels)
        return token_labels, token_to_offsets_map
    else:
        return token_to_offsets_map


def clean_predicted_text(
    text, offsets
):  ##Remove punctuations from outputs beginning or end
    new_offsets = []
    pred_ranges = _contiguous_ranges(offsets)
    for range_ in pred_ranges:
        start = range_[0]
        end = range_[-1]

        while start < end:
            if text[start] in string.punctuation or text[end] in string.punctuation:
                if text[start] in string.punctuation:
                    start += 1
                if text[end] in string.punctuation:
                    end -= 1
            else:
                break
        new_offsets += list(range(start, end + 1))
    return new_offsets


def get_text_spans(text, offsets):
    text_spans = []
    ranges = _contiguous_ranges(offsets)
    for range_ in ranges:
        text_spans.append(text[range_[0] : range_[1] + 1])
    return text_spans


def dev():
    train_file = "./data/tsd_train.csv"
    dev_file = "./data/tsd_trial.csv"

    train = read_datafile(train_file)
    dev = read_datafile(dev_file)

    reduced_train = []
    for i in train:
        if i not in dev:
            reduced_train.append(i)

    ## Tune Threshold on Dev
    reduced_train_token_labels, reduced_train_offset_mapping = list(
        zip(
            *[
                convert_spans_to_token_labels(text, spans)
                for spans, text in reduced_train
            ]
        )
    )

    dev_token_labels, dev_offset_mapping = list(
        zip(*[convert_spans_to_token_labels(text, spans) for spans, text in dev])
    )

    reduced_train_tokens = [
        [
            word.lower().translate(
                str.maketrans("", "", string.punctuation)
            )  ## Remove Punctuation and make into lower case
            for word in text.split()
        ]
        for spans, text in reduced_train
    ]
    dev_tokens = [
        [
            word.lower().translate(
                str.maketrans("", "", string.punctuation)
            )  ## Remove Punctuation and make into lower case
            for word in text.split()
        ]
        for spans, text in dev
    ]
    reduced_train_token_labels_oh = [
        to_categorical(train_token_label, num_classes=3)
        for train_token_label in reduced_train_token_labels
    ]
    dev_token_labels_oh = [
        to_categorical(dev_token_label, num_classes=3)
        for dev_token_label in dev_token_labels
    ]

    rnnsl = RNNSL()

    run_df = rnnsl.fit(
        reduced_train_tokens,
        reduced_train_token_labels_oh,
        validation_data=(dev_tokens, dev_token_labels_oh),
    )
    run_df.to_csv("RNNSL_Run.csv", index=False)
    # rnnsl.set_up_preprocessing(reduced_train_tokens)
    # rnnsl.model = rnnsl.build()

    val_data = (dev_tokens, dev_token_labels)
    rnnsl.tune_threshold(val_data, f1_score)
    print("=" * 80)
    print("Threshold: ", rnnsl.threshold)
    token_predictions = rnnsl.get_toxic_offsets(
        val_data[0],
    )  ## Word Level Toxic Offsets
    print("=" * 80)
    print(
        "F1_score Word Wise on Dev Tokens :",
        np.mean(
            [
                f1_score(token_predictions[i], val_data[1][i][:128])
                for i in range(len(val_data[1]))
            ]
        ),
    )
    print("=" * 80)

    # dev_offset_mapping #map token index to offsets
    offset_predictions = []
    for example in range(len(dev_tokens)):
        offset_predictions.append([])
        for token in range(len(dev_tokens[example][:128])):
            if token_predictions[example][token] == rnnsl.toxic_label:
                offset_predictions[-1] += list(
                    range(
                        dev_offset_mapping[example][token][0],
                        dev_offset_mapping[example][token][1],
                    )
                )
    dev_spans = [spans for spans, text in dev]
    dev_texts = [text for spans, text in dev]
    new_offset_predictions = [
        clean_predicted_text(text, offsets)
        for offsets, text in zip(offset_predictions, dev_texts)
    ]

    for i in range(20):
        ground_offsets = dev_spans[i]
        old_offsets = offset_predictions[i]
        new_offsets = new_offset_predictions[i]
        text = dev_texts[i]
        print("Text: ", text)
        print("Ground: ", get_text_spans(text, ground_offsets))
        print("Preds: ", get_text_spans(text, old_offsets))
        print("Clean Preds: ", get_text_spans(text, new_offsets))

    avg_dice_score = np.mean(
        [f1(preds, gold) for preds, gold in zip(new_offset_predictions, dev_spans)]
    )

    print("=" * 80)
    print("Avg Dice Score on Dev: ", avg_dice_score)
    print("=" * 80)


def predict():
    train_file = "./data/tsd_train.csv"
    dev_file = "./data/tsd_trial.csv"
    test_file = "./data/tsd_test.csv"

    train = read_datafile(train_file)
    dev = read_datafile(dev_file)

    reduced_train = []
    for i in train:
        if i not in dev:
            reduced_train.append(i)

    ## Tune Threshold on Dev
    reduced_train_token_labels, reduced_train_offset_mapping = list(
        zip(
            *[
                convert_spans_to_token_labels(text, spans)
                for spans, text in reduced_train
            ]
        )
    )

    dev_token_labels, dev_offset_mapping = list(
        zip(*[convert_spans_to_token_labels(text, spans) for spans, text in dev])
    )

    reduced_train_tokens = [
        [
            word.lower().translate(
                str.maketrans("", "", string.punctuation)
            )  ## Remove Punctuation and make into lower case
            for word in text.split()
        ]
        for spans, text in reduced_train
    ]
    dev_tokens = [
        [
            word.lower().translate(
                str.maketrans("", "", string.punctuation)
            )  ## Remove Punctuation and make into lower case
            for word in text.split()
        ]
        for spans, text in dev
    ]
    reduced_train_token_labels_oh = [
        to_categorical(train_token_label, num_classes=3)
        for train_token_label in reduced_train_token_labels
    ]
    dev_token_labels_oh = [
        to_categorical(dev_token_label, num_classes=3)
        for dev_token_label in dev_token_labels
    ]

    rnnsl = RNNSL()

    run_df = rnnsl.fit(
        reduced_train_tokens,
        reduced_train_token_labels_oh,
        validation_data=(dev_tokens, dev_token_labels_oh),
    )
    run_df.to_csv("RNNSL_Run.csv", index=False)
    # rnnsl.set_up_preprocessing(reduced_train_tokens)
    # rnnsl.model = rnnsl.build()

    val_data = (dev_tokens, dev_token_labels)
    rnnsl.tune_threshold(val_data, f1_score)
    print("=" * 80)
    print("Threshold: ", rnnsl.threshold)
    token_predictions = rnnsl.get_toxic_offsets(
        val_data[0],
    )  ## Word Level Toxic Offsets
    print("=" * 80)
    print(
        "F1_score Word Wise on Dev Tokens :",
        np.mean(
            [
                f1_score(token_predictions[i], val_data[1][i][:128])
                for i in range(len(val_data[1]))
            ]
        ),
    )
    print("=" * 80)

    # dev_offset_mapping #map token index to offsets
    offset_predictions = []
    for example in range(len(dev_tokens)):
        offset_predictions.append([])
        for token in range(len(dev_tokens[example][:128])):
            if token_predictions[example][token] == rnnsl.toxic_label:
                offset_predictions[-1] += list(
                    range(
                        dev_offset_mapping[example][token][0],
                        dev_offset_mapping[example][token][1],
                    )
                )
    dev_spans = [spans for spans, text in dev]
    dev_texts = [text for spans, text in dev]
    new_offset_predictions = [
        clean_predicted_text(text, offsets)
        for offsets, text in zip(offset_predictions, dev_texts)
    ]

    for i in range(20):
        ground_offsets = dev_spans[i]
        old_offsets = offset_predictions[i]
        new_offsets = new_offset_predictions[i]
        text = dev_texts[i]
        print("Text: ", text)
        print("Ground: ", get_text_spans(text, ground_offsets))
        print("Preds: ", get_text_spans(text, old_offsets))
        print("Clean Preds: ", get_text_spans(text, new_offsets))

    avg_dice_score = np.mean(
        [f1(preds, gold) for preds, gold in zip(new_offset_predictions, dev_spans)]
    )

    print("=" * 80)
    print("Avg Dice Score on Dev: ", avg_dice_score)
    print("=" * 80)

    ## Test predictions
    print("=" * 80)
    print("Training on both train and dev for predictions!")
    print("=" * 80)
    combo = reduced_train + dev

    combo_token_labels, combo_offset_mapping = list(
        zip(*[convert_spans_to_token_labels(text, spans) for spans, text in combo])
    )
    combo_tokens = [
        [
            word.lower().translate(
                str.maketrans("", "", string.punctuation)
            )  ## Remove Punctuation and make into lower case
            for word in text.split()
        ]
        for spans, text in combo
    ]
    combo_token_labels_oh = [
        to_categorical(combo_token_label, num_classes=3)
        for combo_token_label in combo_token_labels
    ]

    rnnsl_2 = RNNSL()
    pred_df = rnnsl_2.fit(combo_tokens, combo_token_labels_oh, do_val=False)
    pred_df.to_csv("RNNSL_Pred.csv", index=False)
    rnnsl_2.threshold = rnnsl.threshold  ##Replace with tuned threshold
    # rnnsl_2.set_up_preprocessing(combo_tokens)
    # rnnsl_2.model = rnnsl_2.build()

    print("Predicting on Test")
    test = read_datafile(test_file, test=True)
    test_tokens = [
        [
            word.lower().translate(
                str.maketrans("", "", string.punctuation)
            )  ## Remove Punctuation and make into lower case
            for word in text.split()
        ]
        for text in test
    ]
    test_offset_mapping = [
        convert_spans_to_token_labels(text, test=True) for text in test
    ]

    check_for_mismatch(test_tokens, test, test_offset_mapping)
    final_token_predictions = rnnsl_2.get_toxic_offsets(test_tokens)

    final_offset_predictions = []
    for example in range(len(test_tokens)):
        final_offset_predictions.append([])
        for token in range(len(test_tokens[example][:128])):
            if final_token_predictions[example][token] == rnnsl_2.toxic_label:
                final_offset_predictions[-1] += list(
                    range(
                        test_offset_mapping[example][token][0],
                        test_offset_mapping[example][token][1],
                    )
                )
    test_texts = [text for text in test]
    new_final_offset_predictions = [
        clean_predicted_text(text, offsets)
        for offsets, text in zip(final_offset_predictions, test_texts)
    ]

    for i in range(20):
        old_offsets = final_offset_predictions[i]
        new_offsets = new_final_offset_predictions[i]
        text = test_texts[i]
        print("Text: ", text)
        print("Preds: ", get_text_spans(text, old_offsets))
        print("Clean Preds: ", get_text_spans(text, new_offsets))

    with open("./test_predictions.txt", "w") as f:
        for i, spans in enumerate(new_final_offset_predictions):
            f.write(f"{i}\t{str(spans)}\n")


if __name__ == "__main__":
    predict()
