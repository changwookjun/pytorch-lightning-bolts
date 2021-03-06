import os
from argparse import ArgumentParser

import pytorch_lightning as pl
import torch

import torch. nn as nn
from torch.nn import functional as F

from pl_bolts.datamodules import (BinaryMNISTDataModule, CIFAR10DataModule,
                                  ImagenetDataModule, MNISTDataModule,
                                  STL10DataModule)
from pl_bolts.models.autoencoders.components import resnet18_encoder, resnet18_decoder
from pl_bolts.models.autoencoders.components import resnet50_encoder, resnet50_decoder


class AE(pl.LightningModule):

    pretrained_urls = {
        'cifar10-resnet18':
            'https://pl-bolts-weights.s3.us-east-2.amazonaws.com/ae/ae-cifar10/checkpoints/epoch%3D96.ckpt'
    }

    def __init__(
        self,
        input_height,
        enc_type='resnet18',
        first_conv=False,
        maxpool1=False,
        enc_out_dim=512,
        kl_coeff=0.1,
        latent_dim=256,
        lr=1e-4,
        **kwargs
    ):
        """
        Standard AE

        Model is available pretrained on different datasets:

        Example::

            # not pretrained
            ae = AE()

            # pretrained on imagenet
            ae = AE.from_pretrained('resnet50-imagenet')

            # pretrained on cifar10
            ae = AE.from_pretrained('resnet18-cifar10')

        Args:

            input_height: height of the images
            enc_type: option between resnet18 or resnet50
            first_conv: use standard kernel_size 7, stride 2 at start or
                replace it with kernel_size 3, stride 1 conv
            maxpool1: use standard maxpool to reduce spatial dim of feat by a factor of 2
            enc_out_dim: set according to the out_channel count of
                encoder used (512 for resnet18, 2048 for resnet50)
            latent_dim: dim of latent space
            lr: learning rate for Adam
        """

        super(AE, self).__init__()

        self.save_hyperparameters()

        self.lr = lr
        self.enc_out_dim = enc_out_dim
        self.latent_dim = latent_dim
        self.input_height = input_height

        valid_encoders = {
            'resnet18': {'enc': resnet18_encoder, 'dec': resnet18_decoder},
            'resnet50': {'enc': resnet50_encoder, 'dec': resnet50_decoder},
        }

        if enc_type not in valid_encoders:
            self.encoder = resnet18_encoder(first_conv, maxpool1)
            self.decoder = resnet18_decoder(self.latent_dim, self.input_height, first_conv, maxpool1)
        else:
            self.encoder = valid_encoders[enc_type]['enc'](first_conv, maxpool1)
            self.decoder = valid_encoders[enc_type]['dec'](self.latent_dim, self.input_height, first_conv, maxpool1)

        self.fc = nn.Linear(self.enc_out_dim, self.latent_dim)

    @staticmethod
    def pretrained_weights_available():
        return list(AE.pretrained_urls.keys())

    def from_pretrained(self, checkpoint_name):
        if checkpoint_name not in AE.pretrained_urls:
            raise KeyError(str(checkpoint_name) + ' not present in pretrained weights.')

        return self.load_from_checkpoint(AE.pretrained_urls[checkpoint_name], strict=False)

    def forward(self, z):
        return self.decoder(z)

    def step(self, batch, batch_idx):
        x, y = batch

        feats = self.encoder(x)
        z = self.fc(feats)
        x_hat = self.decoder(z)

        loss = F.mse_loss(x_hat, x, reduction='mean')

        return loss, {"loss": loss}

    def training_step(self, batch, batch_idx):
        loss, logs = self.step(batch, batch_idx)
        result = pl.TrainResult(minimize=loss)
        result.log_dict(
            {f"train_{k}": v for k, v in logs.items()}, on_step=True, on_epoch=False
        )
        return result

    def validation_step(self, batch, batch_idx):
        loss, logs = self.step(batch, batch_idx)
        result = pl.EvalResult(checkpoint_on=loss)
        result.log_dict({f"val_{k}": v for k, v in logs.items()})
        return result

    def configure_optimizers(self):
        return torch.optim.Adam(self.parameters(), lr=self.lr)

    @staticmethod
    def add_model_specific_args(parent_parser):
        parser = ArgumentParser(parents=[parent_parser], add_help=False)

        parser.add_argument("--enc_type", type=str, default='resnet18', help="resnet18/resnet50")
        parser.add_argument("--first_conv", action='store_true')
        parser.add_argument("--maxpool1", action='store_true')
        parser.add_argument("--lr", type=float, default=1e-4)

        parser.add_argument(
            "--enc_out_dim", type=int, default=512,
            help="512 for resnet18, 2048 for bigger resnets, adjust for wider resnets"
        )
        parser.add_argument("--latent_dim", type=int, default=256)

        parser.add_argument("--batch_size", type=int, default=256)
        parser.add_argument("--num_workers", type=int, default=8)
        parser.add_argument("--data_dir", type=str, default=".")

        parser.add_argument("--gpus", type=int, default=1)
        parser.add_argument("--max_epochs", type=int, default=200)
        parser.add_argument("--max_steps", type=int, default=-1)
        parser.add_argument("--fast_dev_run", action='store_true')

        return parser


def cli_main(args=None):
    from pl_bolts.callbacks import LatentDimInterpolator, TensorboardGenerativeModelImageSampler

    # cli_main()

    parser = ArgumentParser()
    parser.add_argument("--dataset", default="cifar10", type=str, help="cifar10, stl10, imagenet")
    script_args, _ = parser.parse_known_args(args)

    if script_args.dataset == "cifar10":
        dm_cls = CIFAR10DataModule
    elif script_args.dataset == "stl10":
        dm_cls = STL10DataModule
    elif script_args.dataset == "imagenet":
        dm_cls = ImagenetDataModule
    else:
        raise ValueError(f"undefined dataset {script_args.dataset}")

    parser = AE.add_model_specific_args(parser)
    args = parser.parse_args(args)

    dm = dm_cls.from_argparse_args(args)
    args.input_height = dm.size()[-1]

    if args.max_steps == -1:
        args.max_steps = None

    model = AE(**vars(args))
    callbacks = [TensorboardGenerativeModelImageSampler()]

    trainer = pl.Trainer.from_argparse_args(args)
    trainer.fit(model, dm)
    return dm, model, trainer


if __name__ == "__main__":
    dm, model, trainer = cli_main()
