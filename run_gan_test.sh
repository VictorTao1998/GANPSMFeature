#!/bin/bash
export PYTHONWARNINGS="ignore"

python -m torch.distributed.launch /cephfs/jianyu/GANPSMFeature/test_cycleGAN_psm.py --config-file /cephfs/jianyu/GANPSMFeature/configs/remote_train_gan.yaml --output /cephfs/jianyu/eval/ganpsm_test --model /cephfs/jianyu/eval/GANPSMFeature_joint/models/model_9999.pth --gan-model /cephfs/jianyu/eval/GANPSMFeature_joint/models/model_13999.pth
