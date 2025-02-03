#!/bin/bash
VENV_PATH=$(poetry env info --path)
exec $VENV_PATH/bin/python main.py