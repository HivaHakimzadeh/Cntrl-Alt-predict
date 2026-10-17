"""Retrain the injection classifier and print how well it does.

    python train.py

Run this after adding rows to data/training_data.csv, then restart the server.
"""
from sklearn.metrics import classification_report
from sklearn.model_selection import StratifiedKFold, cross_val_predict

from classifier import DATA_PATH, MODEL_PATH, build_pipeline, load_dataset, train

texts, labels = load_dataset()
print(f"{len(texts)} examples ({sum(labels)} injection, {len(labels) - sum(labels)} benign) from {DATA_PATH}")

cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=0)
predictions = cross_val_predict(build_pipeline(), texts, labels, cv=cv)
print("\n5-fold cross-validation:")
print(classification_report(labels, predictions, target_names=["benign", "injection"], digits=3))

train()
print(f"Saved model to {MODEL_PATH}")
