''''Trains student network using distillation and attention transfer (depending on alpha and beta)'''
from __future__ import print_function
import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F
import torch.backends.cudnn as cudnn
from tqdm import tqdm
import torchvision
import torchvision.transforms as transforms
import json
import argparse
from torch.autograd import Variable
import wide_resnet
import os

parser = argparse.ArgumentParser(description='Training a CIFAR10 student')

# System params
parser.add_argument('--GPU', default='0,1', type=str,help='GPU to use')
parser.add_argument('--student_checkpoint', '-s', default='/disk/scratch/ecrowley/torch/checkpoints/student_statelya.t7',type=str, help='checkpoint to save/load student')
parser.add_argument('--teacher_checkpoint', '-t', default='/disk/scratch/ecrowley/torch/checkpoints/teacher_state.t7',type=str, help='checkpoint to load in teacher')

# Network params
parser.add_argument('--wrn_depth', default=16, type=float, help='depth for WRN')
parser.add_argument('--wrn_width', default=1, type=float, help='width for WRN')

# Mode params
parser.add_argument('--resume', '-r', action='store_true', help='resume from checkpoint')
parser.add_argument('--eval', '-e', action='store_true', help='evaluate rather than train')

# Learning params
parser.add_argument('--lr', default=0.1, type=float, help='learning rate')
parser.add_argument('--lr_decay_ratio', default=0.2, type=float, help='learning rate decay')
parser.add_argument('--temperature', default=4, type=float, help='temp for KD')
parser.add_argument('--alpha', default=0.9, type=float, help='alpha for KD')
parser.add_argument('--epoch_step', default='[60,120,160]', type=str,
                    help='json list with epochs to drop lr on')
parser.add_argument('--epochs', default=200, type=int, metavar='N',
                    help='number of total epochs to run')
parser.add_argument('--weightDecay', default=0.0005, type=float)
parser.add_argument('--test_every', default=10, type=float, help='test every N epochs')

args = parser.parse_args()
print (vars(args))

os.environ["CUDA_VISIBLE_DEVICES"] = args.GPU

use_cuda = torch.cuda.is_available()
assert use_cuda, 'Error: No CUDA!'

best_acc = 0  # best test accuracy
start_epoch = 0  # start from epoch 0 or last checkpoint epoch
epoch_step = json.loads(args.epoch_step)

# Data and loaders
print('==> Preparing data..')
transform_train = transforms.Compose([
    transforms.RandomCrop(32, padding=4),
    transforms.RandomHorizontalFlip(),
    transforms.ToTensor(),
    transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010)),
])
transform_test = transforms.Compose([
    transforms.ToTensor(),
    transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010)),
])

trainset = torchvision.datasets.CIFAR10(root='/disk/scratch/datasets/cifar',
                                        train=True, download=True, transform=transform_train)
trainloader = torch.utils.data.DataLoader(trainset, batch_size=128, shuffle=True, num_workers=2)

testset = torchvision.datasets.CIFAR10(root='/disk/scratch/datasets/cifar',
                                       train=False, download=True, transform=transform_test)
testloader = torch.utils.data.DataLoader(testset, batch_size=100, shuffle=False, num_workers=2)

# Model
if args.resume or args.eval:
    # Load checkpoint.
    print('==> Loading student from checkpoint..')
    checkpoint = torch.load(args.student_checkpoint)
    net = checkpoint['net']
    best_acc = checkpoint['acc']
    start_epoch = checkpoint['epoch']
else:
    print('==> Building model..')
    net = wide_resnet.WideResNet(args.wrn_depth, 10, args.wrn_width, dropRate=0)

# Load teacher checkpoint.
if not args.eval:
    print('==> Loading teacher from checkpoint..')
    assert os.path.isfile(args.teacher_checkpoint), 'Error: no checkpoint found!'
    checkpoint = torch.load(args.teacher_checkpoint)

    teach = wide_resnet.WideResNet(16, 10, 2, dropRate=0)
    teach.load_state_dict(checkpoint['state_dict'])
    print ('==> Loaded teacher..')

    teach = teach.cuda(0)

    for param in teach.parameters():
        param.requires_grad = False


net = net.cuda(1)
criterion = nn.CrossEntropyLoss()


def create_optimizer(lr):
    print('creating optimizer with lr = %0.5f' % lr)
    return torch.optim.SGD(net.parameters(), lr, 0.9, weight_decay=args.weightDecay)
optimizer = create_optimizer(args.lr)


def distillation(y, teacher_scores, labels, T, alpha):
    return F.kl_div(F.log_softmax(y/T), F.softmax(teacher_scores/T)) * (T*T * 2. * alpha)\
           + F.cross_entropy(y, labels) * (1. - alpha)


def at(x):
    return F.normalize(x.pow(2).mean(1).view(x.size(0), -1))


def at_loss(x, y):
    return (at(x) - at(y)).pow(2).mean()


def l1_loss(x):
    return torch.abs(x).mean()


# Training the student
def train(epoch):
    print('\nEpoch: %d' % epoch)
    net.train()
    teach.eval()
    train_loss = 0
    correct = 0
    total = 0
    for batch_idx, (inputs, targets) in enumerate(tqdm(trainloader)):
        inputs_teacher, inputs_student = Variable(inputs.cuda(0)), Variable(inputs.cuda(1))
        targets = Variable(targets.cuda(1))
        outputs_student = net(inputs_student)
        outputs_teacher = Variable(teach(inputs_teacher).data.cuda(1))
        loss = distillation(outputs_student, outputs_teacher, targets, args.temperature, args.alpha)

        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        train_loss += loss.data[0]
        _, predicted = torch.max(outputs_student.data, 1)
        total += targets.size(0)
        correct += predicted.eq(targets.data).cpu().sum()

        if batch_idx % 100 == 0:
            print('\nLoss: %.3f | Acc: %.3f%% (%d/%d)\n'
            % (train_loss/(batch_idx+1), 100.*correct/total, correct, total))


def test():
    global best_acc
    net.eval()
    test_loss = 0
    correct = 0
    total = 0
    for batch_idx, (inputs, targets) in enumerate(testloader):

        inputs, targets = inputs.cuda(1), targets.cuda(1)
        inputs, targets = Variable(inputs, volatile=True), Variable(targets)
        outputs = net(inputs)
        loss = criterion(outputs, targets)

        test_loss += loss.data[0]
        _, predicted = torch.max(outputs.data, 1)
        total += targets.size(0)
        correct += predicted.eq(targets.data).cpu().sum()

    print('Loss: %.3f | Acc: %.3f%% (%d/%d)' % (test_loss/(batch_idx+1), 100.*correct/total, correct, total))

    # Save checkpoint.
    if not args.eval:
        acc = 100.*correct/total
        if acc > best_acc:
            print('Saving..')
            state = {
                'net': net,
                'acc': acc,
                'epoch': epoch,
            }
            print('SAVED!')
            torch.save(state, args.student_checkpoint)
            best_acc = acc


def test_teacher():
    global best_acc
    teach.eval()
    test_loss = 0
    correct = 0
    total = 0
    for batch_idx, (inputs, targets) in enumerate(testloader):
        if use_cuda:
            inputs, targets = inputs.cuda(0), targets.cuda(0)
        inputs, targets = Variable(inputs, volatile=True), Variable(targets)
        outputs = teach(inputs)
        loss = criterion(outputs, targets)

        test_loss += loss.data[0]
        _, predicted = torch.max(outputs.data, 1)
        total += targets.size(0)
        correct += predicted.eq(targets.data).cpu().sum()

    print('Loss: %.3f | Acc: %.3f%% (%d/%d)'
        % (test_loss/(batch_idx+1), 100.*correct/total, correct, total))


if not args.eval:

    print('===> Assessing teacher to make sure it''s decent!')
    test_teacher()

    for epoch in tqdm(range(args.epochs)):
        if epoch in epoch_step:
            lr = optimizer.param_groups[0]['lr']
            optimizer = create_optimizer(lr * args.lr_decay_ratio)
        train(epoch)
        if (epoch + 1) % args.test_every == 0 or epoch == 0:
            test()
else:
    print('Evaluating student...')
    test()
