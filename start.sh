#!/bin/bash
mkdir -p models
wget -nc https://github.com/notAI-tech/NudeNet/releases/download/v0/classifier_model.onnx -P models/
python bot.py
