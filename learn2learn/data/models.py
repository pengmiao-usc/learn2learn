#!/usr/bin/env python3

import torch as th
import torch.nn as nn

from torchvision.models.resnet import conv3x3

from scipy.stats import truncnorm


"""
NOTE: TF and pytorch batchnorm use different hyperparam values.
      Also: https://github.com/cbfinn/maml/issues/10
      Concretely, CBF doesn't keep track of running stats, and only uses beta
      (the bias) of batch norm.
"""


def truncated_normal_(tensor, mean=0.0, std=1.0):
    # PT doesn't have truncated normal.
    # https://discuss.pytorch.org/t/implementing-truncated-normal-initializer/4778/18
    values = truncnorm.rvs(-2, 2, size=tensor.shape)
    values = mean + std * values
    tensor.copy_(th.from_numpy(values))
    return tensor


def maml_fc_init_(module):
    if hasattr(module, 'weight') and module.weight is not None:
        truncated_normal_(module.weight.data, mean=0.0, std=0.01)
    if hasattr(module, 'bias') and module.bias is not None:
        nn.init.constant_(module.bias.data, 0.0)
    return module


def maml_init_(module):
    nn.init.xavier_uniform_(module.weight.data, gain=1.0)
    nn.init.constant_(module.bias.data, 0.0)
    return module


class AddBias(nn.Module):

    def __init__(self, size):
        super(AddBias, self).__init__()
        self.bias = nn.Parameter(th.zeros(size))

    def forward(self, x):
        return x + self.bias


class MAMLLinearBlock(nn.Module):

    def __init__(self, input_size, output_size):
        super(MAMLLinearBlock, self).__init__()
        self.relu = nn.ReLU()
        self.normalize = nn.BatchNorm1d(output_size,
                                        affine=True,
                                        momentum=0.999,
                                        eps=1e-3,
                                        track_running_stats=False,
                                        )
        self.linear = nn.Linear(input_size, output_size)
        maml_fc_init_(self.linear)

    def forward(self, x):
        x = self.linear(x)
        x = self.normalize(x)
        x = self.relu(x)
        return x


class MAMLConvBlock(nn.Module):

    def __init__(self, in_channels, out_channels, kernel_size, max_pool=True, max_pool_factor=1.0):
        super(MAMLConvBlock, self).__init__()
        stride = (int(2 * max_pool_factor), int(2 * max_pool_factor))
        if max_pool:
            self.max_pool = nn.MaxPool2d(kernel_size=stride,  # TODO: Correct ?
                                         stride=stride,
                                         ceil_mode=False,  # pad='VALID' (?)
                                         )
            stride = (1, 1)
        else:
            self.max_pool = lambda x: x
        self.normalize = nn.BatchNorm2d(out_channels,
                                        affine=True,
                                        eps=1e-3,
                                        momentum=0.999,
                                        track_running_stats=False,
                                        )
        # TODO: Add BN bias.
        self.relu = nn.ReLU()

        self.conv = nn.Conv2d(in_channels,
                              out_channels,
                              kernel_size,
                              stride=stride,
                              padding=1,
                              bias=True)
        maml_init_(self.conv)

    def forward(self, x):
        x = self.conv(x)
        x = self.normalize(x)
        x = self.relu(x)
        x = self.max_pool(x)
        return x


class MAMLConvBase(nn.Sequential):

    """
    NOTE:
        Omniglot: hidden=64, channels=1, no max_pool
        MiniImagenet: hidden=32, channels=3, max_pool
    """

    def __init__(self, output_size, hidden=64, channels=1, max_pool=False, layers=4, mp_factor=1.0):
        core = [MAMLConvBlock(channels, hidden, (3, 3), max_pool=max_pool, max_pool_factor=mp_factor),]
        for l in range(layers - 1):
            core.append(MAMLConvBlock(hidden, hidden, (3, 3), max_pool=max_pool, max_pool_factor=mp_factor))
        super(MAMLConvBase, self).__init__(*core)


class OmniglotCNN(nn.Module):

    def __init__(self, output_size=5, hidden_size=64, layers=4):
        super(OmniglotCNN, self).__init__()
        self.hidden_size = hidden_size
        self.base = MAMLConvBase(output_size=hidden_size,
                                 hidden=hidden_size,
                                 channels=1,
                                 max_pool=False,
                                 layers=layers)
        self.linear = nn.Linear(hidden_size, output_size, bias=True)
        self.linear.weight.data.normal_()
        self.linear.bias.data.mul_(0.0)

    def forward(self, x):
        x = self.base(x.view(-1, 1, 28, 28))
        x = x.mean(dim=[2, 3])
        x = self.linear(x)
        return x


class MiniImagenetCNN(nn.Module):

    def __init__(self, output_size, hidden_size=32, layers=4):
        super(MiniImagenetCNN, self).__init__()
        self.base = MAMLConvBase(output_size=hidden_size,
                                 hidden=hidden_size,
                                 channels=3,
                                 max_pool=True,
                                 layers=layers,
                                 mp_factor=4//layers)
        self.linear = nn.Linear(25*hidden_size, output_size, bias=True)
        maml_init_(self.linear)
        self.hidden_size = hidden_size

    def forward(self, x):
        x = self.base(x)
        x = self.linear(x.view(-1, 25*self.hidden_size))
        return x


class MAMLFC(nn.Sequential):

    def __init__(self, input_size, output_size, sizes=None):
        if sizes is None:
            sizes = [256, 128, 64, 64]
        layers = [MAMLLinearBlock(input_size, sizes[0]), ]
        for s_i, s_o in zip(sizes[:-1], sizes[1:]):
            layers.append(MAMLLinearBlock(s_i, s_o))
        layers.append(maml_fc_init_(nn.Linear(sizes[-1], output_size)))
        super(MAMLFC, self).__init__(*layers)
#        super(MAMLFC, self).__init__(
#            MAMLLinearBlock(input_size, 256),
#            MAMLLinearBlock(256, 128),
#            MAMLLinearBlock(128, 64),
#            MAMLLinearBlock(64, 64),
#            maml_fc_init_(nn.Linear(64, output_size)),
#        )
        self.input_size = input_size

    def forward(self, x):
        return super(MAMLFC, self).forward(x.view(-1, self.input_size))
