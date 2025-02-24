#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Python version: 3.6


import os
import copy
import time
import pickle
import numpy as np
from tqdm import tqdm

import torch
from tensorboardX import SummaryWriter

from options import args_parser
from update import LocalUpdate, test_inference
from models import MLP, CNNMnist, CNNFashion_Mnist, CNNCifar, vgg11
from utils import get_dataset, average_weights, exp_details


if __name__ == '__main__':
    start_time = time.time()

    # define paths
    path_project = os.path.abspath('..')
    logger = SummaryWriter('../logs')

    args = args_parser()
    exp_details(args)
    print("args: ", args)

    # if args.gpu_id:
    #     torch.cuda.set_device(args.gpu_id)
    device = 'cuda' if int(args.gpu) != 0 else 'cpu'

    # load dataset and user groups
    train_dataset, test_dataset, user_groups = get_dataset(args)

    #Randomly set attackers
    attackers = []
    if float(args.attack_frac) > 0:
        #Make train_dataset mutable for pertrubation
        for i in range(len(train_dataset)):
            train_dataset[i] = list(train_dataset[i])
        #Randomly select malicous users
        num_attackers = int(args.attack_frac * args.num_users)
        attackers = list(np.random.choice(range(args.num_users), num_attackers, replace=False))
    print ("Attackers: ", attackers)

    # BUILD MODEL
    if args.model == 'cnn':
        # Convolutional neural netork
        if args.dataset == 'mnist':
            global_model = CNNMnist(args=args)
        elif args.dataset == 'fmnist':
            global_model = CNNFashion_Mnist(args=args)
        elif args.dataset == 'cifar':
            global_model = CNNCifar(args=args)

    elif args.model == 'mlp':
        # Multi-layer preceptron
        img_size = train_dataset[0][0].shape
        len_in = 1
        for x in img_size:
            len_in *= x
            global_model = MLP(dim_in=len_in, dim_hidden=64,
                               dim_out=args.num_classes)
    elif args.model == 'vgg':
        if args.dataset == 'cifar':
            global_model = vgg11()
        else:
            exit('Error: We only use VGG models for cifar dataset')
    else:
        exit('Error: unrecognized model')

    # Set the model to train and send it to device.
    global_model.to(device)
    global_model.train()
    print(global_model)

    # copy weights
    global_weights = global_model.state_dict()

    # Training
    train_loss, train_accuracy = [], []
    val_acc_list, net_list = [], []
    cv_loss, cv_acc = [], []
    print_every = 2
    val_loss_pre, counter = 0, 0
    total_grads = []

    for epoch in tqdm(range(args.epochs)):
        local_weights, local_losses, local_grads = [], [], {}
        print(f'\n | Global Training Round : {epoch+1} |\n')

        global_model.train()
        m = max(int(args.frac * args.num_users), 1)
        idxs_users = np.random.choice(range(args.num_users), m, replace=False)

        for idx in idxs_users:
            malicous = True if idx in attackers else False
            local_model = LocalUpdate(args=args, dataset=train_dataset,
                                      idxs=user_groups[idx], logger=logger,attacker=malicous)
            w, loss, grads = local_model.update_weights(
                model=copy.deepcopy(global_model), global_round=epoch)
            local_weights.append(copy.deepcopy(w))
            local_losses.append(copy.deepcopy(loss))
            #Append current user grads to local grads together with the label
            local_grads[idx] = [copy.deepcopy(grads),int(malicous)]

        #Check if local_grads works
        total_grads.append(copy.deepcopy(local_grads))
        sample_idx  = list(local_grads.keys())[0]
        print("1 device grad size: ", len( local_grads[sample_idx] ))
        print("Round", epoch," grads: ", len(local_grads))
        print("Total grads: ", len(total_grads))

        # update global weights
        global_weights = average_weights(local_weights)

        # update global weights
        global_model.load_state_dict(global_weights)

        loss_avg = sum(local_losses) / len(local_losses)
        train_loss.append(loss_avg)

        # Calculate avg training accuracy over all non-malicous users at every epoch
        list_acc, list_loss = [], []
        global_model.eval()
        for c in range(args.num_users):
            if c not in attackers:
                local_model = LocalUpdate(args=args, dataset=train_dataset,
                                        idxs=user_groups[idx], logger=logger)
                acc, loss = local_model.inference(model=global_model)
                list_acc.append(acc)
                list_loss.append(loss)
        train_accuracy.append(sum(list_acc)/len(list_acc))

        # print global training loss after every 'i' rounds
        if (epoch+1) % print_every == 0:
            print(f' \nAvg Training Stats after {epoch+1} global rounds:')
            print(f'Training Loss : {np.mean(np.array(train_loss))}')
            print('Train Accuracy: {:.2f}% \n'.format(100*train_accuracy[-1]))

    # Test inference after completion of training
    test_acc, test_loss = test_inference(args, global_model, test_dataset)

    print(f' \n Results after {args.epochs} global rounds of training:')
    print("|---- Avg Train Accuracy: {:.2f}%".format(100*train_accuracy[-1]))
    print("|---- Test Accuracy: {:.2f}%".format(100*test_acc))

    # Saving the objects train_loss and train_accuracy:
    file_name = './save/objects/{}_{}_users[{}]_rounds[{}]_frac[{}]_iid[{}]_local_ep[{}]_local_bs[{}]_attck_frac[{}]'.\
        format(args.dataset, args.model, args.num_users, args.epochs, args.frac, args.iid,
               args.local_ep, args.local_bs,args.attack_frac)

    with open(file_name + ".pkl", 'wb') as f:
        pickle.dump([train_loss, train_accuracy], f)
    
    with open(file_name + "_grads.pkl", 'wb') as f:
        pickle.dump(total_grads,f)

    print('\n Total Run Time: {0:0.4f}'.format(time.time()-start_time))

    # PLOTTING (optional)
    import matplotlib
    import matplotlib.pyplot as plt
    matplotlib.use('Agg')

    file_name_loss = './save/plots/{}_{}_users[{}]_rounds[{}]_frac[{}]_iid[{}]_local_ep[{}]_local_bs[{}]_attck_frac[{}]_loss.png'.\
        format(args.dataset, args.model, args.num_users, args.epochs, args.frac, args.iid,
               args.local_ep, args.local_bs,args.attack_frac)
    
    file_name_acc = './save/plots/{}_{}_users[{}]_rounds[{}]_frac[{}]_iid[{}]_local_ep[{}]_local_bs[{}]_attck_frac[{}]_acc.png'.\
        format(args.dataset, args.model, args.num_users, args.epochs, args.frac, args.iid,
               args.local_ep, args.local_bs,args.attack_frac)

    # Plot Loss curve
    plt.figure()
    plt.title('Training Loss vs Communication rounds')
    plt.plot(range(len(train_loss)), train_loss, color='r')
    plt.ylabel('Training loss')
    plt.xlabel('Communication Rounds')
    plt.savefig(file_name_loss)
    
    # Plot Average Accuracy vs Communication rounds
    plt.figure()
    plt.title('Average Accuracy vs Communication rounds')
    plt.plot(range(len(train_accuracy)), train_accuracy, color='k')
    plt.ylabel('Average Accuracy')
    plt.xlabel('Communication Rounds')
    plt.savefig(file_name_acc)
