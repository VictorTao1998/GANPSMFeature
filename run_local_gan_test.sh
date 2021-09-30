#!/bin/bash
export PYTHONWARNINGS="ignore"

python -m torch.distributed.launch /code/test_cycleGAN_psm.py \
--config-file /code/configs/local_test_gan.yaml --output /data/logs/ganpsm_test_deep_sim \
--model /data/ganmodel/model_best.pth --gan-model /data/ganmodel/model_best.pth \
--exclude-bg --exclude-zeros
