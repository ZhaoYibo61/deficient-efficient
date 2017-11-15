''''Teach the student iteratively, using the teacher activations as input.'''
from __future__ import print_function
import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F
import torchvision
import torchvision.transforms as transforms
import json
import argparse
from torch.autograd import Variable
from models.wide_resnet import*
import os
from tqdm import tqdm

from funcs import *

parser = argparse.ArgumentParser(description='Student/teacher training')
parser.add_argument('dataset', type=str, choices=['cifar10', 'cifar100'], help='Choose between Cifar10/100.')
parser.add_argument('--resume', '-r', action='store_true', help='resume from checkpoint')
parser.add_argument('--GPU', default='3', type=str,help='GPU to use')
parser.add_argument('--student_checkpoint', '-s', default='wrn_40_2_student_KT',type=str, help='checkpoint to save/load student')
parser.add_argument('--teacher_checkpoint', '-t', default='cifar100_T',type=str, help='checkpoint to load in teacher')

#network stuff
parser.add_argument('--wrn_depth', default=40, type=int, help='depth for WRN')
parser.add_argument('--wrn_width', default=2, type=float, help='width for WRN')
parser.add_argument('--block',default='Basic',type=str, help='blocktype')

parser.add_argument('conv',
                    choices=['Conv','ConvB2','ConvB4','ConvB8','ConvB16','DConv',
                             'Conv2x2','DConvB2','DConvB4','DConvB8','DConvB16','DConv3D','DConvG2','DConvG4','DConvG8','DConvG16'
                        ,'custom','DConvA2','DConvA4','DConvA8','DConvA16','G2B2','G2B4','G4B2','G4B4','G8B2','G8B4','G16B2','G16B4','A2B2','A4B2','A8B2','A16B2'],
                    type=str, help='Conv type')
parser.add_argument('--customconv',default=['Conv_Conv_ConvB16'],type=str)
parser.add_argument('--AT_split', default=1, type=int, help='group splitting for AT loss')

#learning stuff
parser.add_argument('--lr', default=0.1, type=float, help='learning rate')
parser.add_argument('--lr_decay_ratio', default=0.2, type=float, help='learning rate decay')
parser.add_argument('--temperature', default=4, type=float, help='temp for KD')
parser.add_argument('--alpha', default=0.9, type=float, help='alpha for KD')
parser.add_argument('--aux_loss', default='AT', type=str, help='AT or SE loss')
parser.add_argument('--beta', default=1e3, type=float, help='beta for AT')
parser.add_argument('--epoch_step', default='[60,120,160]', type=str,
                    help='json list with epochs to drop lr on')
parser.add_argument('--epochs', default=200, type=int, metavar='N',
                    help='number of total epochs to run')
parser.add_argument('--batch_size', default=128, type=int,
                    help='minibatch size')
parser.add_argument('--weightDecay', default=0.0005, type=float)

args = parser.parse_args()

criterion = nn.CrossEntropyLoss()
sqerror = nn.MSELoss()

def create_optimizer(lr, net):
    print('creating optimizer with lr = %0.5f' % lr)
    return torch.optim.SGD(net.parameters(), lr, 0.9, weight_decay=args.weightDecay)

def train_student(net, teach):
    """Trains the student, iteratively"""
    net.train()
    teach.eval()
    train_loss = 0
    activation_loss = 0
    correct = 0
    total = 0
    for batch_idx, (inputs, targets) in enumerate(tqdm(trainloader)):
        inputs = Variable(inputs.cuda())
        targets = Variable(targets.cuda())
        outputs_teacher, ints_teacher = teach(inputs)
        #outputs_student, ints_student = net(inputs)
        outputs_student = partial_fprop(net, ints_teacher[-1], 2)

        # If alpha is 0 then this loss is just a cross entropy.
        loss = distillation(outputs_student, outputs_teacher, targets, args.temperature, 1.0)

        #Add an attention tranfer loss for each intermediate. Let's assume the default is three (as in the original
        #paper) and adjust the beta term accordingly.

        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        train_loss += loss.data[0]
        _, predicted = torch.max(outputs_student.data, 1)
        total += targets.size(0)
        correct += predicted.eq(targets.data).cpu().sum()

        for i,a in enumerate([inputs]+ints_teacher[:-1]):
            outputs_student = partial_fprop(net, a, i-1)
            loss = sqerror(outputs_student, ints_teacher[i])
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            activation_loss += loss.data[0]

    #print(len(ints_student))
    print('\nLoss: %.3f | Activation Match: %.3f | Acc: %.3f%% (%d/%d)\n' % (train_loss/(batch_idx+1), activation_loss/(batch_idx+1), 100.*correct/total, correct, total))

def train_student_AT(net, teach):
    net.train()
    teach.eval()
    train_loss = 0
    correct = 0
    total = 0
    for batch_idx, (inputs, targets) in enumerate(tqdm(trainloader)):
        inputs = Variable(inputs.cuda())
        targets = Variable(targets.cuda())
        outputs_student, ints_student = net(inputs)
        outputs_teacher, ints_teacher = teach(inputs)

        # If alpha is 0 then this loss is just a cross entropy.
        loss = distillation(outputs_student, outputs_teacher, targets, args.temperature, args.alpha)

        #Add an attention tranfer loss for each intermediate. Let's assume the default is three (as in the original
        #paper) and adjust the beta term accordingly.

        adjusted_beta = (args.beta*3)/len(ints_student)
        for i in range(len(ints_student)):
            loss += adjusted_beta * aux_loss(ints_student[i], ints_teacher[i])

        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        train_loss += loss.data[0]
        _, predicted = torch.max(outputs_student.data, 1)
        total += targets.size(0)
        correct += predicted.eq(targets.data).cpu().sum()
    print(len(ints_student))
    print('\nLoss: %.3f | Acc: %.3f%% (%d/%d)\n' % (train_loss/(batch_idx+1), 100.*correct/total, correct, total))

def test(net, checkpoint=None):
    global best_acc
    net.eval()
    test_loss = 0
    correct = 0
    total = 0
    for batch_idx, (inputs, targets) in enumerate(testloader):

        inputs, targets = inputs.cuda(), targets.cuda()
        inputs, targets = Variable(inputs, volatile=True), Variable(targets)
        outputs = net(inputs)
        if isinstance(outputs,tuple):
            outputs = outputs[0]

        loss = criterion(outputs, targets)

        test_loss += loss.data[0]
        _, predicted = torch.max(outputs.data, 1)
        total += targets.size(0)
        correct += predicted.eq(targets.data).cpu().sum()

    test_losses.append(test_loss/(batch_idx+1))
    test_accs.append(100.*correct/total)


    print('Test Loss: %.3f | Acc: %.3f%% (%d/%d)' % (test_loss/(batch_idx+1), 100.*correct/total, correct, total))

    if checkpoint:
        # Save checkpoint.
        acc = 100.*correct/total
        if acc > best_acc:
            best_acc = acc

        print('Saving..')
        state = {
            'net': net,
            'acc': acc,
            'epoch': epoch,
            'best_acc': best_acc,
            'width': args.wrn_width,
            'depth': args.wrn_depth,
            'conv_type': conv,
            'train_losses': train_losses,
            'train_accs': train_accs,
            'test_losses': test_losses,
            'test_accs': test_accs,
        }
        print('SAVED!')
        torch.save(state, 'checkpoints/%s.t7' % checkpoint)


def decay_optimizer_lr(optimizer, decay_rate):
    # callback to set the learning rate in an optimizer, without rebuilding the whole optimizer
    for param_group in optimizer.param_groups:
        param_group['lr'] = param_group['lr'] * decay_rate
    return optimizer

# Stuff happens from here:
if __name__ == '__main__':
    # Stuff happens from here:
    if args.conv != 'custom':
        conv = args.conv
    else:
        conv = args.customconv.split('_')

    print(conv)

    if args.aux_loss == 'AT':
        aux_loss = at_loss
    elif args.aux_loss == 'SE':
        aux_loss = se_loss

    print(aux_loss)

    print(vars(args))
    os.environ["CUDA_VISIBLE_DEVICES"] = args.GPU

    use_cuda = torch.cuda.is_available()
    assert use_cuda, 'Error: No CUDA!'

    test_losses  = []
    train_losses = []
    test_accs    = []
    train_accs   = []

    best_acc = 0
    start_epoch = 0
    epoch_step = json.loads(args.epoch_step)

    # Data and loaders
    print('==> Preparing data..')
    if args.dataset == 'cifar10':
        num_classes = 10
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
                                                train=True, download=False, transform=transform_train)
        testset = torchvision.datasets.CIFAR10(root='/disk/scratch/datasets/cifar',
                                               train=False, download=False, transform=transform_test)

    elif args.dataset == 'cifar100':
        num_classes = 100
        transform_train = transforms.Compose([
            transforms.RandomCrop(32, padding=4),
            transforms.RandomHorizontalFlip(),
            transforms.ToTensor(),
            transforms.Normalize((0.5071, 0.4866, 0.4409), (0.2009, 0.1984, 0.2023)),
        ])
        transform_test = transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize((0.5071, 0.4866, 0.4409), (0.2009, 0.1984, 0.2023)),
        ])

        trainset = torchvision.datasets.CIFAR100(root='/disk/scratch/datasets/cifar100',
                                                train=True, download=True, transform=transform_train)
        testset = torchvision.datasets.CIFAR100(root='/disk/scratch/datasets/cifar100',
                                               train=False, download=True, transform=transform_test)



    trainloader = torch.utils.data.DataLoader(trainset, batch_size=args.batch_size, shuffle=True, num_workers=2)
    testloader = torch.utils.data.DataLoader(testset, batch_size=100, shuffle=False, num_workers=2)

    criterion = nn.CrossEntropyLoss()

    print('Loading teacher...')
    teach = WideResNetAT(int(args.wrn_depth), int(args.wrn_width), num_classes=num_classes, s=args.AT_split)
    if os.path.exists('state_dicts/%s.t7' % args.teacher_checkpoint):
        state_dict_new = torch.load('state_dicts/%s.t7' % args.teacher_checkpoint)
    else:
        teach_checkpoint = torch.load('checkpoints/%s.t7' % args.teacher_checkpoint)
        state_dict_old = teach_checkpoint['net'].state_dict()
        state_dict_new = teach.state_dict()
        old_keys = [v for v in state_dict_old]
        new_keys = [v for v in state_dict_new]
        for i,_ in enumerate(state_dict_old):
            state_dict_new[new_keys[i]] = state_dict_old[old_keys[i]]
        torch.save(state_dict_new, 'state_dicts/%s.t7' % args.teacher_checkpoint)

    teach.load_state_dict(state_dict_new)
    teach = teach.cuda()
    # Very important to explicitly say we require no gradients for the teacher network
    for param in teach.parameters():
        param.requires_grad = False
    #print("Testing teacher...")
    #test(teach)

    if args.resume:
        print('Mode Student: Loading student and continuing training...')
        student_checkpoint = torch.load('checkpoints/%s.t7' % args.student_checkpoint)
        start_epoch = student_checkpoint['epoch']
        student = student_checkpoint['net']
    else:
        print('Mode Student: Making a student network from scratch and training it...')
        student = WideResNetAT(args.wrn_depth, args.wrn_width,  num_classes=num_classes, dropRate=0, convtype=conv,
                               s=args.AT_split, blocktype=args.block).cuda()

    student = student.cuda()
    optimizer = optim.SGD(student.parameters(), lr=args.lr, momentum=0.9, weight_decay=args.weightDecay)

    # This bit is stupid but we need to decay the learning rate depending on the epoch
    for e in range(0, start_epoch):
        if e in epoch_step:
            optimizer = decay_optimizer_lr(optimizer, args.lr_decay_ratio)

    for epoch in tqdm(range(start_epoch, args.epochs)):
        print('Student Epoch %d:' % epoch)
        print('Learning rate is %s' % [v['lr'] for v in optimizer.param_groups][0])
        if epoch in epoch_step:
            optimizer = decay_optimizer_lr(optimizer, args.lr_decay_ratio)
        if epoch < 3:
            train_student(student, teach)
        else:
            train_student_AT(student, teach)
        test(student, args.student_checkpoint)