"""Train an optional research-only mammography image classifier.

This scaffold supports labeled mammography images only (not X-rays or
ultrasounds). It requires separately sourced, licensed, de-identified images;
no image dataset or trained image weights are included with this project.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data" / "mammography"
MODEL_DIR = ROOT / "models"
CLASS_NAMES = ["benign", "malignant"]
IMAGE_SIZE = (224, 224)
BATCH_SIZE = 16
SUPPORTED_TRAINING_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".gif"}


def _check_dataset():
    expected = [DATA_DIR / split / label for split in ("train", "validation") for label in CLASS_NAMES]
    missing = [path for path in expected if not path.is_dir()]
    empty = [
        path for path in expected
        if path.is_dir()
        and not any(
            file.is_file()
            and file.suffix.lower() in SUPPORTED_TRAINING_SUFFIXES
            and file.stat().st_size > 0
            for file in path.iterdir()
        )
    ]
    if missing or empty:
        details = []
        if missing:
            details.append("Missing folders: " + ", ".join(str(path.relative_to(ROOT)) for path in missing))
        if empty:
            details.append("Empty class folders: " + ", ".join(str(path.relative_to(ROOT)) for path in empty))
        raise SystemExit(
            "No complete mammography dataset is available. Add properly licensed, "
            "de-identified images to data/mammography/{train,validation}/{benign,malignant}. "
            + " ".join(details)
        )


def main() -> None:
    _check_dataset()
    try:
        import tensorflow as tf
    except ImportError as error:
        raise SystemExit(
            "TensorFlow is optional. Install a version compatible with your Python/macOS "
            "using requirements-image.txt before training."
        ) from error

    train_data = tf.keras.utils.image_dataset_from_directory(
        DATA_DIR / "train",
        class_names=CLASS_NAMES,
        image_size=IMAGE_SIZE,
        batch_size=BATCH_SIZE,
        label_mode="categorical",
        shuffle=True,
        seed=42,
    )
    validation_data = tf.keras.utils.image_dataset_from_directory(
        DATA_DIR / "validation",
        class_names=CLASS_NAMES,
        image_size=IMAGE_SIZE,
        batch_size=BATCH_SIZE,
        label_mode="categorical",
        shuffle=False,
    )

    autotune = tf.data.AUTOTUNE
    train_data = train_data.prefetch(autotune)
    validation_data = validation_data.prefetch(autotune)

    base = tf.keras.applications.MobileNetV2(
        input_shape=(*IMAGE_SIZE, 3), include_top=False, weights="imagenet"
    )
    base.trainable = False
    inputs = tf.keras.Input(shape=(*IMAGE_SIZE, 3))
    values = tf.keras.applications.mobilenet_v2.preprocess_input(inputs)
    values = base(values, training=False)
    values = tf.keras.layers.GlobalAveragePooling2D()(values)
    values = tf.keras.layers.Dropout(0.25)(values)
    outputs = tf.keras.layers.Dense(len(CLASS_NAMES), activation="softmax")(values)
    model = tf.keras.Model(inputs, outputs)
    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=1e-3),
        loss="categorical_crossentropy",
        metrics=["accuracy", tf.keras.metrics.AUC(name="auc")],
    )

    MODEL_DIR.mkdir(exist_ok=True)
    callbacks = [
        tf.keras.callbacks.EarlyStopping(
            patience=4, restore_best_weights=True, monitor="val_loss"
        ),
        tf.keras.callbacks.ModelCheckpoint(
            str(MODEL_DIR / "image_model.keras"),
            save_best_only=True,
            monitor="val_loss",
        ),
    ]
    model.fit(train_data, validation_data=validation_data, epochs=20, callbacks=callbacks)
    (MODEL_DIR / "image_class_names.json").write_text(
        json.dumps(CLASS_NAMES, indent=2) + "\n", encoding="utf-8"
    )
    print("Saved optional mammography model and class mapping.")
    print("Validation scores are exploratory and are not independent clinical validation.")
    print("This model is not suitable for X-ray or ultrasound images or for patient care.")


if __name__ == "__main__":
    main()