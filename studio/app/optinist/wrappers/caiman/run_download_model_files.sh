#!/bin/bash
set -e

#
# This function is a port of
#   studio.app.optinist.wrappers.caiman.cnmf.util_download_model_files()
#
download_model_files() {
  # Base URL for downloading model files
  BASE_URL="https://raw.githubusercontent.com/flatironinstitute/CaImAn/v1.9.12/model"

  # List of model files to download
  MODEL_FILES=(
      "cnn_model.h5"
      "cnn_model.h5.pb"
      "cnn_model.json"
      "cnn_model_online.h5"
      "cnn_model_online.h5.pb"
      "cnn_model_online.json"
  )

  # Create caiman_data directory in home directory
  CAIMAN_DATA_DIR="$HOME/caiman_data"
  if [ ! -d "$CAIMAN_DATA_DIR" ]; then
    mkdir -p "$CAIMAN_DATA_DIR"
  fi

  # Create model directory
  MODEL_DIR="$CAIMAN_DATA_DIR/model"
  if [ ! -d "$MODEL_DIR" ]; then
    mkdir -p "$MODEL_DIR"
  fi

  # Download any model file that is missing
  for MODEL in "${MODEL_FILES[@]}"; do
      FILE_PATH="$MODEL_DIR/$MODEL"
      if [ ! -f "$FILE_PATH" ]; then
          echo "Downloading $MODEL"
          curl --fail --location --connect-timeout 10 --retry 5 --retry-delay 3 "$BASE_URL/$MODEL" -o "$FILE_PATH"
      fi
  done
}

# call downloading func
download_model_files
