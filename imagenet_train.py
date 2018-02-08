''''Writing everything into one script..'''
from __future__ import print_function
import torch
import torch.nn as nn
import torch.optim as optim
import torch.optim.lr_scheduler as lr_scheduler
import torch.nn.functional as F
import torchvision
import torchvision.transforms as transforms
import json
import argparse
from torch.autograd import Variable
from models.wide_resnet import WideResNet, parse_options
import os
import imp
from tqdm import tqdm
from tensorboardX import SummaryWriter
import torch.nn.parallel

from funcs import *

parser = argparse.ArgumentParser(description='Student/teacher training')
parser.add_argument('dataset', type=str, choices=['cifar10', 'cifar100', 'imagenet'], help='Choose between Cifar10/100/imagenet.')
parser.add_argument('mode', choices=['KD','AT'], type=str, help='Learn with KD, AT, or train a teacher')
parser.add_argument('--imagenet_loc', default='/disk/scratch_ssd/imagenet',type=str, help='folder containing imagenet train and val folders')
parser.add_argument('--workers', default=16, type=int, help='No. of data loading workers. Make this high for imagenet')
parser.add_argument('--resume', '-r', action='store_true', help='resume from checkpoint')
parser.add_argument('--student_checkpoint', '-s', default='imagenet',type=str, help='checkpoint to save/load student')
parser.add_argument('--print_every', '-p', default=100,type=int, help='')

#network stuff

parser.add_argument('--module', default=None, type=str, help='path to file containing custom Conv and maybe Block module definitions')
parser.add_argument('--blocktype', default='Basic',type=str, help='blocktype used if specify a --conv')
parser.add_argument('--conv',
                    choices=['Conv','ConvB2','ConvB4','ConvB8','ConvB16','DConv',
                             'Conv2x2','DConvB2','DConvB4','DConvB8','DConvB16','DConv3D','DConvG2','DConvG4','DConvG8','DConvG16'
                        ,'custom','DConvA2','DConvA4','DConvA8','DConvA16','G2B2','G2B4','G4B2','G4B4','G8B2','G8B4','G16B2','G16B4','A2B2','A4B2','A8B2','A16B2'],
                    default=None, type=str, help='Conv type')
parser.add_argument('--AT_split', default=1, type=int, help='group splitting for AT loss')

#learning stuff
parser.add_argument('--lr', default=0.1, type=float, help='learning rate')
parser.add_argument('--lr_decay_ratio', default=0.1, type=float, help='learning rate decay')
parser.add_argument('--temperature', default=4, type=float, help='temp for KD')
parser.add_argument('--alpha', default=0., type=float, help='alpha for KD')
parser.add_argument('--aux_loss', default='AT', type=str, help='AT or SE loss')
parser.add_argument('--beta', default=1e3, type=float, help='beta for AT')
parser.add_argument('--epoch_step', default='[30,60,90]', type=str,
                    help='json list with epochs to drop lr on')
parser.add_argument('--epochs', default=100, type=int, metavar='N',
                    help='number of total epochs to run')
parser.add_argument('--batch_size', default=256, type=int,
                    help='minibatch size')
parser.add_argument('--weightDecay', default=1e-4, type=float)

args = parser.parse_args()

writer = SummaryWriter()


def create_optimizer(lr,net):
    print('creating optimizer with lr = %0.5f' % lr)
    return torch.optim.SGD(net.parameters(), lr, 0.9, weight_decay=args.weightDecay)


def train_student_KD(net, teach):
    net.train()
    teach.eval()
    train_loss = 0
    correct = 0
    total = 0
    for batch_idx, (inputs, targets) in enumerate(tqdm(trainloader)):
        inputs = Variable(inputs.cuda())
        targets = Variable(targets.cuda())
        outputs_student, _ = net(inputs)
        outputs_teacher, _ = teach(inputs)
        loss = distillation(outputs_student, outputs_teacher, targets, args.temperature, args.alpha)

        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        train_loss += loss.data[0]
        _, predicted = torch.max(outputs_student.data, 1)
        total += targets.size(0)
        correct += predicted.eq(targets.data).cpu().sum()
        writer.add_scalar('train_loss', train_loss / (batch_idx + 1), epoch)
        writer.add_scalar('train_acc', 100. * correct / total, epoch)

    print('\nLoss: %.3f | Acc: %.3f%% (%d/%d)\n' % (train_loss/(batch_idx+1), 100.*correct/total, correct, total))



# Training the student
def train_student_AT(net, teach):
    net.train()
    teach.eval()
    train_loss = 0
    correct = 0
    total = 0
    top1=0
    top5=0

    for batch_idx, (inputs, targets) in enumerate(trainloader):


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
        prec1, prec5 = accuracy(outputs_student.data.cpu(), targets.data.cpu(), topk=(1, 5))
        total += targets.size(0)
        top1 += prec1
        top5 += prec5

        if batch_idx % args.print_every == 0:

            print('Epoch %d |Train Batch %d of %d |Loss: %.3f | Top1Error: %.3f%% | Top5Error: %.3f%%' %
                  (epoch,batch_idx, len(trainloader), train_loss / total, 100. - top1 / (batch_idx + 1),
                   100. - top5 / (batch_idx + 1)))

    writer.add_scalar('train_loss', train_loss / (batch_idx + 1), epoch)
    writer.add_scalar('train_top1', 100. - top1 / (batch_idx + 1), epoch)
    writer.add_scalar('train_top5', 100. - top5 / (batch_idx + 1), epoch)
    train_losses.append(train_loss / (batch_idx + 1))
    train_top1.append(100. * correct / total)
    train_top5.append(100. * correct / total)

        # _, predicted = torch.max(outputs_student.data, 1)
        # total += targets.size(0)
        # correct += predicted.eq(targets.data).cpu().sum()
        # writer.add_scalar('train_loss', train_loss / (batch_idx + 1), epoch)
        # writer.add_scalar('train_acc', 100. * correct / total, epoch)



def test(net, checkpoint=None):
    net.eval()
    test_loss = 0
    correct = 0
    total = 0
    top1 = 0
    top5 = 0
    for batch_idx, (inputs, targets) in enumerate(testloader):

        inputs, targets = inputs.cuda(), targets.cuda()
        inputs, targets = Variable(inputs, volatile=True), Variable(targets)
        outputs, _ = net(inputs)
        if isinstance(outputs,tuple):
            outputs = outputs[0]

        loss = criterion(outputs, targets)

        test_loss += loss.data[0]
        prec1, prec5 = accuracy(outputs.data.cpu(), targets.data.cpu(), topk=(1, 5))
        total += targets.size(0)
        top1 += prec1
        top5 += prec5

        if batch_idx % args.print_every == 0:
            print('Epoch % d | Test Batch %d of %d |Loss: %.3f | Top1Error: %.3f%% | Top5Error: %.3f%%' %
                  (epoch, batch_idx, len(testloader),test_loss/total, 100. - top1/(batch_idx+1), 100. - top5/(batch_idx+1)))


    writer.add_scalar('test_loss', test_loss / (batch_idx + 1), epoch)
    writer.add_scalar('test_top1', 100. - top1/(batch_idx+1), epoch)
    writer.add_scalar('test_top5', 100. - top5/(batch_idx+1), epoch)
    test_losses.append(test_loss/(batch_idx+1))
    test_top1.append(100.*correct/total)
    test_top5.append(100.*correct/total)


    if checkpoint:
        # Save checkpoint.
        acc = 100.*correct/total


        print('Saving..')
        state = {
            'net': net.state_dict(),
            'acc': acc,
            'epoch': epoch,
            'conv': args.conv,
            'blocktype': args.blocktype,
            'module': args.module,
            'train_losses': train_losses,
            'train_top1': train_top1,
            'train_top5': train_top5,
            'test_losses': test_losses,
            'test_top1': test_top1,
            'test_top5': test_top5,
        }
        print('SAVED!')
        torch.save(state, 'checkpoints/%s.t7' % checkpoint)



def what_conv_block(conv, blocktype, module):
    if conv is not None:
        Conv, Block = parse_options(conv, blocktype)
    elif module is not None:
        conv_module = imp.new_module('conv')
        with open(module, 'r') as f:
            exec(f.read(), conv_module.__dict__)
        Conv = conv_module.Conv
        try:
            Block = conv_module.Block
        except AttributeError:
            # if the module doesn't implement a custom block,
            # use default option
            _, Block = parse_options('Conv', args.blocktype)
    else:
        raise ValueError("You must specify either an existing conv option, or supply your own module to import")
    return Conv, Block

if __name__ == '__main__':
    # Stuff happens from here:
    Conv, Block = what_conv_block(args.conv, args.blocktype, args.module)

    if args.aux_loss == 'AT':
        aux_loss = at_loss
    elif args.aux_loss == 'SE':
        aux_loss = se_loss

    print(vars(args))


    use_cuda = torch.cuda.is_available()
    assert use_cuda, 'Error: No CUDA!'

    test_losses  = []
    train_losses = []
    test_top1    = []
    test_top5    = []
    train_top1   = []
    train_top5   = []

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

    elif args.dataset == 'imagenet':
        num_classes = 1000
        traindir = os.path.join(args.imagenet_loc, 'train')
        valdir = os.path.join(args.imagenet_loc, 'val')
        normalize = transforms.Normalize(mean=[0.485, 0.456, 0.406],
                                         std=[0.229, 0.224, 0.225])

        transform_train = transforms.Compose([
            transforms.RandomResizedCrop(224),
            transforms.RandomHorizontalFlip(),
            transforms.ToTensor(),
            normalize,
        ])
        transform_test = transforms.Compose([
            transforms.Resize(256),
            transforms.CenterCrop(224),
            transforms.ToTensor(),
            normalize,
        ])

        trainset = torchvision.datasets.ImageFolder(traindir, transform_train)
        testset = torchvision.datasets.ImageFolder(valdir, transform_test)



    trainloader = torch.utils.data.DataLoader(trainset, batch_size=args.batch_size, shuffle=True,
                                              num_workers=args.workers,
                                              pin_memory = True if args.dataset == 'imagenet' else False)
    testloader = torch.utils.data.DataLoader(testset, batch_size=100, shuffle=False,
                                             num_workers=args.workers,
                                             pin_memory=True if args.dataset == 'imagenet' else False)

    criterion = nn.CrossEntropyLoss()

    def load_network(loc):
        net_checkpoint = torch.load(loc)
        start_epoch = net_checkpoint['epoch']
        SavedConv, SavedBlock = what_conv_block(net_checkpoint['conv'],
                net_checkpoint['blocktype'], net_checkpoint['module'])
        net = WideResNet(args.wrn_depth, args.wrn_width, SavedConv, SavedBlock, num_classes=num_classes, dropRate=0).cuda()
        net.load_state_dict(net_checkpoint['net'])
        return net, start_epoch


    if args.mode == 'KD':
        print('Mode Student: First, load a teacher network and check it performs decently...,')
        teach, start_epoch = load_network('checkpoints/%s.t7' % args.teacher_checkpoint)
        # Very important to explicitly say we require no gradients for the teacher network
        for param in teach.parameters():
            param.requires_grad = False
        test(teach)
        if args.resume:
            print('KD: Loading student and continuing training...')
            student, start_epoch = load_network('checkpoints/%s.t7' % args.student_checkpoint)
        else:
            print('KD: Making a student network from scratch and training it...')
            student = WideResNet(args.wrn_depth, args.wrn_width, Conv, Block, num_classes=num_classes, dropRate=0).cuda()
        optimizer = optim.SGD(student.parameters(), lr=args.lr, momentum=0.9, weight_decay=args.weightDecay)
        scheduler = lr_scheduler.MultiStepLR(optimizer, milestones=epoch_step, gamma=args.lr_decay_ratio)

        # This bit is stupid but we need to decay the learning rate depending on the epoch
        for e in range(0, start_epoch):
            scheduler.step()

        for epoch in tqdm(range(start_epoch, args.epochs)):
            scheduler.step()

            print('Student Epoch %d:' % epoch)
            print('Learning rate is %s' % [v['lr'] for v in optimizer.param_groups][0])
            writer.add_scalar('learning_rate', [v['lr'] for v in optimizer.param_groups][0], epoch)

            train_student_KD(student, teach)
            test(student, args.student_checkpoint)


    elif args.mode == 'AT':
        print('AT (+optional KD): First, load a teacher network and convert for attention transfer')
        teach = resnet34(pretrained=True)
        teach.cuda()
        teach = torch.nn.DataParallel(teach).cuda()
        epoch = 0
        print(epoch)
        # Very important to explicitly say we require no gradients for the teacher network
        for param in teach.parameters():
            param.requires_grad = False

        if args.resume:
            print('Mode Student: Loading student and continuing training...')
            student, start_epoch = load_network('checkpoints/%s.t7' % args.student_checkpoint)
        else:
            print('Mode Student: Making a student network from scratch and training it...')
            student = torch.nn.DataParallel(resnet18(pretrained=False)).cuda()

        optimizer = optim.SGD(student.parameters(), lr=args.lr, momentum=0.9, weight_decay=args.weightDecay)
        scheduler = lr_scheduler.MultiStepLR(optimizer, milestones=epoch_step, gamma=args.lr_decay_ratio)

        # This bit is stupid but we need to decay the learning rate depending on the epoch
        for e in range(0, start_epoch):
            scheduler.step()


        for epoch in tqdm(range(start_epoch, args.epochs)):
            scheduler.step()

            print('Student Epoch %d:' % epoch)
            print('Learning rate is %s' % [v['lr'] for v in optimizer.param_groups][0])
            writer.add_scalar('learning_rate', [v['lr'] for v in optimizer.param_groups][0], epoch)
            train_student_AT(student, teach)
            test(student, args.student_checkpoint)
            #test(student, args.student_checkpoint)
