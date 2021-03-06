# -*- coding=utf-8 -*-
__author__ = 'max'

import sys
import os

sys.path.append(".")
sys.path.append("..")

import time
import argparse
import uuid
import json

import numpy as np
import torch
from torch.nn.utils import clip_grad_norm_
from torch.optim import Adam, SGD, Adamax
from neuronlp2.io import get_logger, conllx_stacked_data
from neuronlp2.models import StackPtrNet
from neuronlp2 import utils
from neuronlp2.io import CoNLLXWriter
from neuronlp2.tasks import parser

uid = uuid.uuid4().hex[:6]


from pytorch_pretrained_bert.optimization import BertAdam, warmup_linear

def main():
    args_parser = argparse.ArgumentParser(description='Tuning with stack pointer parser')
    args_parser.add_argument('--mode', choices=['RNN', 'LSTM', 'GRU', 'FastLSTM'], help='architecture of rnn', required=True)
    args_parser.add_argument('--num_epochs', type=int, default=200, help='Number of training epochs')
    args_parser.add_argument('--batch_size', type=int, default=64, help='Number of sentences in each batch')
    args_parser.add_argument('--decoder_input_size', type=int, default=256, help='Number of input units in decoder RNN.')
    args_parser.add_argument('--hidden_size', type=int, default=256, help='Number of hidden units in RNN')
    args_parser.add_argument('--arc_space', type=int, default=128, help='Dimension of tag space')
    args_parser.add_argument('--type_space', type=int, default=128, help='Dimension of tag space')
    args_parser.add_argument('--encoder_layers', type=int, default=1, help='Number of layers of encoder RNN')
    args_parser.add_argument('--decoder_layers', type=int, default=1, help='Number of layers of decoder RNN')
    args_parser.add_argument('--num_filters', type=int, default=50, help='Number of filters in CNN')
    # NOTE: action='store_true' is just to set ON
    args_parser.add_argument('--pos', action='store_true', help='use part-of-speech embedding.')
    args_parser.add_argument('--char', action='store_true', help='use character embedding and CNN.')
    args_parser.add_argument('--pos_dim', type=int, default=50, help='Dimension of POS embeddings')
    args_parser.add_argument('--char_dim', type=int, default=50, help='Dimension of Character embeddings')
    # NOTE: arg MUST be one of choices(when specified)
    args_parser.add_argument('--opt', choices=['adam', 'sgd', 'adamax'], help='optimization algorithm')
    args_parser.add_argument('--learning_rate', type=float, default=0.001, help='Learning rate')
    args_parser.add_argument('--decay_rate', type=float, default=0.75, help='Decay rate of learning rate')
    args_parser.add_argument('--max_decay', type=int, default=9, help='Number of decays before stop')
    args_parser.add_argument('--double_schedule_decay', type=int, default=5, help='Number of decays to double schedule')
    args_parser.add_argument('--clip', type=float, default=5.0, help='gradient clipping')
    args_parser.add_argument('--gamma', type=float, default=0.0, help='weight for regularization')
    args_parser.add_argument('--epsilon', type=float, default=1e-8, help='epsilon for adam or adamax')
    args_parser.add_argument('--coverage', type=float, default=0.0, help='weight for coverage loss')
    args_parser.add_argument('--p_rnn', nargs=2, type=float, required=True, help='dropout rate for RNN')
    args_parser.add_argument('--p_in', type=float, default=0.33, help='dropout rate for input embeddings')
    args_parser.add_argument('--p_out', type=float, default=0.33, help='dropout rate for output layer')
    args_parser.add_argument('--label_smooth', type=float, default=1.0, help='weight of label smoothing method')
    args_parser.add_argument('--skipConnect', action='store_true', help='use skip connection for decoder RNN.')
    args_parser.add_argument('--grandPar', action='store_true', help='use grand parent.')
    args_parser.add_argument('--sibling', action='store_true', help='use sibling.')
    args_parser.add_argument('--prior_order', choices=['inside_out', 'left2right', 'deep_first', 'shallow_first'], help='prior order of children.', required=True)
    args_parser.add_argument('--schedule', type=int, help='schedule for learning rate decay')
    args_parser.add_argument('--unk_replace', type=float, default=0., help='The rate to replace a singleton word with UNK')
    args_parser.add_argument('--punctuation', nargs='+', type=str, help='List of punctuations')
    args_parser.add_argument('--beam', type=int, default=1, help='Beam size for decoding')
    args_parser.add_argument('--word_embedding', choices=['glove', 'senna', 'sskip', 'polyglot', 'NNLM'], help='Embedding for words', required=True)
    args_parser.add_argument('--word_path', help='path for word embedding dict')
    args_parser.add_argument('--freeze', action='store_true', help='frozen the word embedding (disable fine-tuning).')    
    args_parser.add_argument('--char_embedding', choices=['random', 'polyglot'], help='Embedding for characters', required=True)
    args_parser.add_argument('--char_path', help='path for character embedding dict')
    args_parser.add_argument('--train')  # "data/POS-penn/wsj/split1/wsj1.train.original"
    args_parser.add_argument('--dev')  # "data/POS-penn/wsj/split1/wsj1.dev.original"
    args_parser.add_argument('--test')  # "data/POS-penn/wsj/split1/wsj1.test.original"
    args_parser.add_argument('--model_path', help='path for saving model file.', required=True)
    args_parser.add_argument('--model_name', help='name for saving model file.', required=True)
    # TODO: to include in logging process
    args_parser.add_argument('--pos_embedding', choices=[1,2,4], type=int, help='Embedding method for korean POS tag', default=2)
    args_parser.add_argument('--pos_path', help='path for pos embedding dict')
    args_parser.add_argument('--elmo', action='store_true', help='use elmo embedding.')
    args_parser.add_argument('--elmo_path', help='path for elmo embedding model.')
    args_parser.add_argument('--elmo_dim', type=int, help='dimension for elmo embedding model')
    #args_parser.add_argument('--fine_tune_path', help='fine tune starting from this state_dict')
    args_parser.add_argument('--model_version', help='previous model version to load')
    #bert2020_boychaboy
    args_parser.add_argument('--bert', action='store_true', help='use elmo embedding.')  # true if use bert(hoon)
    args_parser.add_argument('--etri_train', help='path for etri data of bert')  # etri train path(hoon)
    args_parser.add_argument('--etri_dev', help='path for etri data of bert')  # etri dev path(hoon)
    args_parser.add_argument('--bert_path', help='path for bert embedding model.')
    args_parser.add_argument('--bert_feature_dim', type=int, help='dimension for bert feature embedding')

    args = args_parser.parse_args()

    logger = get_logger("PtrParser")

    mode = args.mode
    train_path = args.train
    dev_path = args.dev
    test_path = args.test
    model_path = args.model_path + uid + '/'   # for numerous experiments
    model_name = args.model_name
    num_epochs = args.num_epochs
    batch_size = args.batch_size
    input_size_decoder = args.decoder_input_size
    hidden_size = args.hidden_size
    arc_space = args.arc_space
    type_space = args.type_space
    encoder_layers = args.encoder_layers
    decoder_layers = args.decoder_layers
    num_filters = args.num_filters
    learning_rate = args.learning_rate
    opt = args.opt
    momentum = 0.9
    betas = (0.9, 0.9)
    eps = args.epsilon
    decay_rate = args.decay_rate
    clip = args.clip
    gamma = args.gamma
    cov = args.coverage
    schedule = args.schedule
    p_rnn = tuple(args.p_rnn)
    p_in = args.p_in
    p_out = args.p_out
    label_smooth = args.label_smooth
    unk_replace = args.unk_replace
    prior_order = args.prior_order
    skipConnect = args.skipConnect
    grandPar = args.grandPar
    sibling = args.sibling
    beam = args.beam
    punctuation = args.punctuation

    freeze = args.freeze
    word_embedding = args.word_embedding
    word_path = args.word_path

    use_char = args.char
    char_embedding = args.char_embedding
    # QUESTION: pretrained vector for char?
    char_path = args.char_path

    use_pos = args.pos    
    pos_embedding = args.pos_embedding
    pos_path = args.pos_path
    pos_dict = None
    pos_dim = args.pos_dim    # NOTE pretrain 있을 경우 pos_dim은 그거 따라감
    if pos_path is not None:
        pos_dict, pos_dim = utils.load_embedding_dict(word_embedding, pos_path)  # NOTE 임시적으로 word_embedding(NNLM)이랑 같은 형식
    word_dict, word_dim = utils.load_embedding_dict(word_embedding, word_path)
    char_dict = None
    char_dim = args.char_dim
    if char_embedding != 'random':
        char_dict, char_dim = utils.load_embedding_dict(char_embedding, char_path)    

    use_elmo = args.elmo
    elmo_path = args.elmo_path
    elmo_dim = args.elmo_dim
    #fine_tune_path = args.fine_tune_path

    #bert 2020(boychaboy)
    use_bert = args.bert
    bert_path = args.bert_path
    bert_feature_dim = args.bert_feature_dim

    if use_bert:
        etri_train_path = args.etri_train
        etri_dev_path = args.etri_dev
    else:
        etri_train_path = None
        etri_dev_path = None

    logger.info("Creating Alphabets")
    alphabet_path = os.path.join(model_path, 'alphabets/')
    model_name = os.path.join(model_path, model_name)
    # min_occurence=1
    data_paths = [dev_path, test_path] if test_path else [dev_path]
    word_alphabet, char_alphabet, pos_alphabet, type_alphabet = conllx_stacked_data.create_alphabets(alphabet_path, train_path, data_paths=data_paths,
                                                                                                      max_vocabulary_size=50000, pos_embedding=pos_embedding, embedd_dict=word_dict)

    num_words = word_alphabet.size() # 30268
    num_chars = char_alphabet.size() # 3545
    num_pos = pos_alphabet.size() # 46
    num_types = type_alphabet.size()  # 39

    logger.info("Word Alphabet Size: %d" % num_words)
    logger.info("Character Alphabet Size: %d" % num_chars)
    logger.info("POS Alphabet Size: %d" % num_pos)
    logger.info("Type Alphabet Size: %d" % num_types)

    logger.info("Reading Data")
    use_gpu = torch.cuda.is_available()

    # data is a list of tuple containing tensors, etc ...
    data_train = conllx_stacked_data.read_stacked_data_to_variable(train_path, word_alphabet, char_alphabet, pos_alphabet, type_alphabet, pos_embedding, use_gpu=1, prior_order=prior_order, elmo = use_elmo, bert=use_bert, etri_path=etri_train_path)
    num_data = sum(data_train[2])

    data_dev = conllx_stacked_data.read_stacked_data_to_variable(dev_path, word_alphabet, char_alphabet, pos_alphabet, type_alphabet, pos_embedding, use_gpu=use_gpu, volatile=True, prior_order=prior_order, elmo = use_elmo, bert=use_bert, etri_path=etri_dev_path)
    if test_path:
        data_test = conllx_stacked_data.read_stacked_data_to_variable(test_path, word_alphabet, char_alphabet, pos_alphabet, type_alphabet, pos_embedding, use_gpu=use_gpu, volatile=True, prior_order=prior_order, elmo = use_elmo)

    punct_set = None
    if punctuation is not None:
        punct_set = set(punctuation)
        logger.info("punctuations(%d): %s" % (len(punct_set), ' '.join(punct_set)))

    def construct_word_embedding_table():
        scale = np.sqrt(3.0 / word_dim)
        table = np.empty([word_alphabet.size(), word_dim], dtype=np.float32)
        # NOTE: UNK 관리!
        table[conllx_stacked_data.UNK_ID, :] = np.zeros([1, word_dim]).astype(np.float32) if freeze else np.random.uniform(-scale, scale, [1, word_dim]).astype(np.float32)
        oov = 0
        for word, index in list(word_alphabet.items()):
            if word in word_dict:
                embedding = word_dict[word]
            elif word.lower() in word_dict:
                embedding = word_dict[word.lower()]
            else:
                # NOTE: words not in pretrained are set to random
                embedding = np.zeros([1, word_dim]).astype(np.float32) if freeze else np.random.uniform(-scale, scale, [1, word_dim]).astype(np.float32)
                oov += 1
            table[index, :] = embedding
        print('word OOV: %d' % oov)
        return torch.from_numpy(table)

    def construct_char_embedding_table():
        if char_dict is None:
            return None

        scale = np.sqrt(3.0 / char_dim)
        table = np.empty([num_chars, char_dim], dtype=np.float32)
        table[conllx_stacked_data.UNK_ID, :] = np.random.uniform(-scale, scale, [1, char_dim]).astype(np.float32)
        oov = 0
        #for char, index, in char_alphabet.items():
        for char, index in list(char_alphabet.items()):
            if char in char_dict:
                embedding = char_dict[char]
            else:
                embedding = np.random.uniform(-scale, scale, [1, char_dim]).astype(np.float32)
                oov += 1
            table[index, :] = embedding
        print('character OOV: %d' % oov)
        return torch.from_numpy(table)

    def construct_pos_embedding_table():
        if pos_dict is None:
            return None

        scale = np.sqrt(3.0 / char_dim)
        table = np.empty([num_pos, pos_dim], dtype=np.float32)
        for pos, index in list(pos_alphabet.items()):
            if pos in pos_dict:
                embedding = pos_dict[pos]
            else:
                embedding = np.random.uniform(-scale, scale, [1, char_dim]).astype(np.float32)
            table[index, :] = embedding
        return torch.from_numpy(table)
    
    word_table = construct_word_embedding_table()
    char_table = construct_char_embedding_table()
    pos_table = construct_pos_embedding_table()

    window = 3
    network = StackPtrNet(word_dim, num_words, char_dim, num_chars, pos_dim, num_pos, num_filters, window,
                          mode, input_size_decoder, hidden_size, encoder_layers, decoder_layers,
                          num_types, arc_space, type_space, pos_embedding,
                          embedd_word=word_table, embedd_char=char_table, embedd_pos=pos_table, p_in=p_in, p_out=p_out,
                          p_rnn=p_rnn, biaffine=True, pos=use_pos, char=use_char, elmo=use_elmo, prior_order=prior_order,
                          skipConnect=skipConnect, grandPar=grandPar, sibling=sibling, elmo_path=elmo_path, elmo_dim=elmo_dim,
                          bert=use_bert, bert_path=bert_path, bert_feature_dim=bert_feature_dim)


    # if fine_tune_path is not None:
    #     pretrained_dict = torch.load(fine_tune_path)
    #     model_dict = network.state_dict()
    #     # select
    #     #model_dict['pos_embedd.weight'] = pretrained_dict['pos_embedd.weight']
    #     model_dict['word_embedd.weight'] = pretrained_dict['word_embedd.weight']
    #     #model_dict['char_embedd.weight'] = pretrained_dict['char_embedd.weight']
    #     network.load_state_dict(model_dict)

    model_ver = args.model_version
    if model_ver is not None:
        savePath = args.model_path + model_ver + 'network.pt'
        network.load_state_dict(torch.load(savePath))
        logger.info('Load model: %s' % (model_ver))

    def save_args():
        arg_path = model_name + '.arg.json'
        arguments = [word_dim, num_words, char_dim, num_chars, pos_dim, num_pos, num_filters, window,
                     mode, input_size_decoder, hidden_size, encoder_layers, decoder_layers,
                     num_types, arc_space, type_space, pos_embedding]
        kwargs = {'p_in': p_in, 'p_out': p_out, 'p_rnn': p_rnn, 'biaffine': True, 'pos': use_pos, 'char': use_char, 'elmo': use_elmo, 'prior_order': prior_order,
                 'skipConnect': skipConnect, 'grandPar': grandPar, 'sibling': sibling}
        json.dump({'args': arguments, 'kwargs': kwargs}, open(arg_path, 'w', encoding="utf-8"), indent=4)

        with open(arg_path + '.raw_args', 'w', encoding="utf-8") as f:
            f.write(str(args))

    if freeze:
        network.word_embedd.freeze()

    if use_gpu:
        network.cuda()

    save_args()

    pred_writer = CoNLLXWriter(word_alphabet, char_alphabet, pos_alphabet, type_alphabet, pos_embedding)
    gold_writer = CoNLLXWriter(word_alphabet, char_alphabet, pos_alphabet, type_alphabet, pos_embedding)

    def generate_optimizer(opt, lr, params):
        params = [param for param in params if param.requires_grad]
        if opt == 'adam':
            return Adam(params, lr=lr, betas=betas, weight_decay=gamma, eps=eps)
        elif opt == 'sgd':
            return SGD(params, lr=lr, momentum=momentum, weight_decay=gamma, nesterov=True)
        elif opt == 'adamax':
            return Adamax(params, lr=lr, betas=betas, weight_decay=gamma, eps=eps)
        else:
            raise ValueError('Unknown optimization algorithm: %s' % opt)

    def generate_differentlr_bert_optimizer(lr, bert_lr, model):
        no_decay = ['bias', 'LayerNorm.weight']

        optimizer_grouped_parameters = [
            {'params': [p for n, p in model.named_parameters() if 'bert_model' not in n]
             }  # ,
            # {'params': [p for n, p in model.named_parameters() if 'bert_' in n],
            # 'lr': bert_lr}
        ]
        '''
        optimizer_grouped_parameters = [
            {'params': [p for n, p in model.named_parameters() if 'bert_model' not in n]},
            #{'params': model.bert_model.parameters(), 'lr': bert_lr}
            {'params': model.bert_morp_feature_embedd.parameters(), 'lr': bert_lr},
            {'params': model.bert_word_feature_embedd.parameters(), 'lr': bert_lr}
        ]
        '''
        for n in optimizer_grouped_parameters:
            print(n)
        # optimizer=Adam(optimizer_grouped_parameters, lr=lr, betas=betas, weight_decay=gamma, eps=eps)
        optimizer = BertAdam(optimizer_grouped_parameters, lr=lr, e=1e-8)
        # scheduler = WarmupLinearSchedule(optimizer, warmup_steps=0, t_total=t_total)
        return optimizer

    def generate_old_bert_optimizer(t_total, bert_lr, model):
        no_decay = ['bias', 'LayerNorm.weight']
        optimizer_grouped_parameters = [
            {'params': [p for n, p in model.named_parameters() if not any(nd in n for nd in no_decay)],
             'weight_decay': gamma},
            {'params': [p for n, p in model.named_parameters() if any(nd in n for nd in no_decay)], 'weight_decay': 0.0}
        ]
        optimizer = BertAdam(optimizer_grouped_parameters, lr=bert_lr, e=1e-8)
        # scheduler = WarmupLinearSchedule(optimizer, warmup_steps=0, t_total=t_total)
        return optimizer

    lr = learning_rate
    # bert_lr = learning_rate
    #  optim = generate_optimizer(opt, lr, network.parameters())
    if use_bert:
        # optim =generate_differentlr_bert_optimizer(lr, lr, network)
        optim = generate_old_bert_optimizer(len(data_train) * num_epochs, lr, network)

    opt_info = 'opt: %s, ' % opt
    if opt == 'adam':
        opt_info += 'betas=%s, eps=%.1e' % (betas, eps)
    elif opt == 'sgd':
        opt_info += 'momentum=%.2f' % momentum
    elif opt == 'adamax':
        opt_info += 'betas=%s, eps=%.1e' % (betas, eps)

    word_status = 'frozen' if freeze else 'fine tune'
    char_status = 'enabled' if use_char else 'disabled'
    pos_status = 'enabled' if use_pos else 'disabled'
    logger.info("Embedding dim: word=%d (%s), char=%d (%s), pos=%d (%s)" % (word_dim, word_status, char_dim, char_status, pos_dim, pos_status))
    logger.info("CNN: filter=%d, kernel=%d" % (num_filters, window))
    logger.info("RNN: %s, num_layer=(%d, %d), input_dec=%d, hidden=%d, arc_space=%d, type_space=%d" % (mode, encoder_layers, decoder_layers, input_size_decoder, hidden_size, arc_space, type_space))
    logger.info("train: cov: %.1f, (#data: %d, batch: %d, clip: %.2f, label_smooth: %.2f, unk_repl: %.2f)" % (cov, num_data, batch_size, clip, label_smooth, unk_replace))
    logger.info("dropout(in, out, rnn): (%.2f, %.2f, %s)" % (p_in, p_out, p_rnn))
    logger.info('prior order: %s, grand parent: %s, sibling: %s, ' % (prior_order, grandPar, sibling))
    logger.info('skip connect: %s, beam: %d' % (skipConnect, beam))
    logger.info(opt_info)

    num_batches = int(num_data / batch_size + 1)  # kwon
    dev_ucorrect = 0.0
    dev_lcorrect = 0.0
    dev_ucomlpete_match = 0.0
    dev_lcomplete_match = 0.0

    dev_ucorrect_nopunc = 0.0
    dev_lcorrect_nopunc = 0.0
    dev_ucomlpete_match_nopunc = 0.0
    dev_lcomplete_match_nopunc = 0.0
    dev_root_correct = 0.0

    best_epoch = 0

    test_ucorrect = 0.0
    test_lcorrect = 0.0
    test_ucomlpete_match = 0.0
    test_lcomplete_match = 0.0

    test_ucorrect_nopunc = 0.0
    test_lcorrect_nopunc = 0.0
    test_ucomlpete_match_nopunc = 0.0
    test_lcomplete_match_nopunc = 0.0
    test_root_correct = 0.0
    test_total = 0
    test_total_nopunc = 0
    test_total_inst = 0
    test_total_root = 0

    patient = 0
    decay = 0
    max_decay = args.max_decay
    double_schedule_decay = args.double_schedule_decay
    for epoch in range(1, num_epochs + 1):
        print('Epoch %d (%s, optim: %s, learning rate=%.6f, eps=%.1e, decay rate=%.2f (schedule=%d, patient=%d, decay=%d (%d, %d))): ' % (
            epoch, mode, opt, lr, eps, decay_rate, schedule, patient, decay, max_decay, double_schedule_decay))
        train_err_arc_leaf = 0.    # QUESTION: leaf and non-leaf?
        train_err_arc_non_leaf = 0.
        train_err_type_leaf = 0.
        train_err_type_non_leaf = 0.
        train_err_cov = 0.
        train_total_leaf = 0.
        train_total_non_leaf = 0.
        start_time = time.time()
        num_back = 0

        network.train()
        for batch in range(1, num_batches + 1):
            # load data #bert2020 [boychaboy]
            input_encoder, input_decoder = conllx_stacked_data.get_batch_stacked_variable(data_train, batch_size, pos_embedding, unk_replace=unk_replace, elmo = use_elmo, bert=use_bert)

            if use_elmo:
                word, char, pos, heads, types, masks_e, lengths_e, word_elmo, word_bert = input_encoder
            else:
                word, char, pos, heads, types, masks_e, lengths_e, word_bert = input_encoder

            stacked_heads, children, sibling, stacked_types, skip_connect, masks_d, lengths_d = input_decoder

            optim.zero_grad()

            if use_elmo:
                loss_arc_leaf, loss_arc_non_leaf, \
                loss_type_leaf, loss_type_non_leaf, \
                loss_cov, num_leaf, num_non_leaf = network.loss(word, char, pos, heads, stacked_heads, children, sibling, stacked_types, label_smooth, skip_connect=skip_connect, mask_e=masks_e, \
                                                                length_e=lengths_e, mask_d=masks_d, length_d=lengths_d, input_word_elmo = word_elmo, input_word_bert = word_bert)
            else:
                loss_arc_leaf, loss_arc_non_leaf, \
                loss_type_leaf, loss_type_non_leaf, \
                loss_cov, num_leaf, num_non_leaf = network.loss(word, char, pos, heads, stacked_heads, children, sibling, stacked_types, label_smooth, \
                                                            skip_connect=skip_connect, mask_e=masks_e, length_e=lengths_e, mask_d=masks_d, length_d=lengths_d, input_word_bert=word_bert)
            loss_arc = loss_arc_leaf + loss_arc_non_leaf
            loss_type = loss_type_leaf + loss_type_non_leaf
            loss = loss_arc + loss_type + cov * loss_cov    # cov is set to 0 by default
            loss.backward()
            clip_grad_norm_(network.parameters(), clip)
            optim.step()

            num_leaf = num_leaf.item()
            num_non_leaf = num_non_leaf.item()
            train_err_arc_leaf += loss_arc_leaf.item() * num_leaf
            train_err_arc_non_leaf += loss_arc_non_leaf.item() * num_non_leaf

            train_err_type_leaf += loss_type_leaf.item() * num_leaf
            train_err_type_non_leaf += loss_type_non_leaf.item() * num_non_leaf

            train_err_cov += loss_cov.item() * (num_leaf + num_non_leaf)
            train_total_leaf += num_leaf
            train_total_non_leaf += num_non_leaf

            time_ave = (time.time() - start_time) / batch
            time_left = (num_batches - batch) * time_ave

            # update log
            if batch % 10 == 0:
                sys.stdout.write("\b" * num_back)
                sys.stdout.write(" " * num_back)
                sys.stdout.write("\b" * num_back)
                err_arc_leaf = train_err_arc_leaf / train_total_leaf
                err_arc_non_leaf = train_err_arc_non_leaf / train_total_non_leaf
                err_arc = err_arc_leaf + err_arc_non_leaf

                err_type_leaf = train_err_type_leaf / train_total_leaf
                err_type_non_leaf = train_err_type_non_leaf / train_total_non_leaf
                err_type = err_type_leaf + err_type_non_leaf

                err_cov = train_err_cov / (train_total_leaf + train_total_non_leaf)

                err = err_arc + err_type + cov * err_cov
                log_info = 'train: %d/%d loss (leaf, non_leaf): %.4f, arc: %.4f (%.4f, %.4f), type: %.4f (%.4f, %.4f), coverage: %.4f, time left (estimated): %.2fs' % (
                    batch, num_batches, err, err_arc, err_arc_leaf, err_arc_non_leaf, err_type, err_type_leaf, err_type_non_leaf, err_cov, time_left)
                sys.stdout.write(log_info)
                sys.stdout.flush()
                num_back = len(log_info)

        sys.stdout.write("\b" * num_back)
        sys.stdout.write(" " * num_back)
        sys.stdout.write("\b" * num_back)
        err_arc_leaf = train_err_arc_leaf / train_total_leaf
        err_arc_non_leaf = train_err_arc_non_leaf / train_total_non_leaf
        err_arc = err_arc_leaf + err_arc_non_leaf

        err_type_leaf = train_err_type_leaf / train_total_leaf
        err_type_non_leaf = train_err_type_non_leaf / train_total_non_leaf
        err_type = err_type_leaf + err_type_non_leaf

        err_cov = train_err_cov / (train_total_leaf + train_total_non_leaf)

        err = err_arc + err_type + cov * err_cov
        print('train: %d loss (leaf, non_leaf): %.4f, arc: %.4f (%.4f, %.4f), type: %.4f (%.4f, %.4f), coverage: %.4f, time: %.2fs' % (
            num_batches, err, err_arc, err_arc_leaf, err_arc_non_leaf, err_type, err_type_leaf, err_type_non_leaf, err_cov, time.time() - start_time))

        # evaluate performance on dev data
        network.eval()
        pred_filename = model_path + 'tmp/pred_dev%d' % (epoch)
        pred_writer.start(pred_filename)
        gold_filename = model_path + 'tmp/gold_dev%d' % (epoch)
        gold_writer.start(gold_filename)

        dev_ucorr = 0.0
        dev_lcorr = 0.0
        dev_total = 0
        dev_ucomlpete = 0.0
        dev_lcomplete = 0.0
        dev_ucorr_nopunc = 0.0
        dev_lcorr_nopunc = 0.0
        dev_total_nopunc = 0
        dev_ucomlpete_nopunc = 0.0
        dev_lcomplete_nopunc = 0.0
        dev_root_corr = 0.0
        dev_total_root = 0.0
        dev_total_inst = 0.0
        for batch in conllx_stacked_data.iterate_batch_stacked_variable(data_dev, batch_size, pos_embedding, type='dev', elmo=use_elmo, bert=use_bert):
            input_encoder, _ = batch
            if use_elmo:
                word, char, pos, heads, types, masks, lengths, word_elmo, word_bert = input_encoder
                heads_pred, types_pred, _, _ = network.decode(word, char, pos, input_word_elmo=word_elmo, mask=masks,
                                                              length=lengths, beam=beam,
                                                              leading_symbolic=conllx_stacked_data.NUM_SYMBOLIC_TAGS, input_word_bert=word_bert)
            else:
                word, char, pos, heads, types, masks, lengths, word_bert = input_encoder
                heads_pred, types_pred, _, _ = network.decode(word, char, pos, mask=masks, length=lengths, beam=beam,
                                                              leading_symbolic=conllx_stacked_data.NUM_SYMBOLIC_TAGS, input_word_bert=word_bert)
            word = word.data.cpu().numpy()
            pos = pos.data.cpu().numpy()
            lengths = lengths.cpu().numpy()
            heads = heads.data.cpu().numpy()
            types = types.data.cpu().numpy()

            pred_writer.write(word, pos, heads_pred, types_pred, lengths, symbolic_root=True)
            gold_writer.write(word, pos, heads, types, lengths, symbolic_root=True)

            stats, stats_nopunc, stats_root, num_inst = parser.eval(word, pos, heads_pred, types_pred, heads, types, word_alphabet, pos_alphabet, lengths, punct_set=punct_set, symbolic_root=True)
            ucorr, lcorr, total, ucm, lcm = stats
            ucorr_nopunc, lcorr_nopunc, total_nopunc, ucm_nopunc, lcm_nopunc = stats_nopunc
            corr_root, total_root = stats_root

            dev_ucorr += ucorr
            dev_lcorr += lcorr
            dev_total += total
            dev_ucomlpete += ucm
            dev_lcomplete += lcm

            dev_ucorr_nopunc += ucorr_nopunc
            dev_lcorr_nopunc += lcorr_nopunc
            dev_total_nopunc += total_nopunc
            dev_ucomlpete_nopunc += ucm_nopunc
            dev_lcomplete_nopunc += lcm_nopunc

            dev_root_corr += corr_root
            dev_total_root += total_root

            dev_total_inst += num_inst

        pred_writer.close()
        gold_writer.close()
        print('W. Punct: ucorr: %d, lcorr: %d, total: %d, uas: %.2f%%, las: %.2f%%, ucm: %.2f%%, lcm: %.2f%%' % (
            dev_ucorr, dev_lcorr, dev_total, dev_ucorr * 100 / dev_total, dev_lcorr * 100 / dev_total, dev_ucomlpete * 100 / dev_total_inst, dev_lcomplete * 100 / dev_total_inst))
        print('Wo Punct: ucorr: %d, lcorr: %d, total: %d, uas: %.2f%%, las: %.2f%%, ucm: %.2f%%, lcm: %.2f%%' % (
            dev_ucorr_nopunc, dev_lcorr_nopunc, dev_total_nopunc, dev_ucorr_nopunc * 100 / dev_total_nopunc,
            dev_lcorr_nopunc * 100 / dev_total_nopunc, dev_ucomlpete_nopunc * 100 / dev_total_inst, dev_lcomplete_nopunc * 100 / dev_total_inst))
        print('Root: corr: %d, total: %d, acc: %.2f%%' % (dev_root_corr, dev_total_root, dev_root_corr * 100 / dev_total_root))

        if dev_ucorrect_nopunc * 1.5 + dev_lcorrect_nopunc < dev_ucorr_nopunc * 1.5 + dev_lcorr_nopunc:
            dev_ucorrect_nopunc = dev_ucorr_nopunc
            dev_lcorrect_nopunc = dev_lcorr_nopunc
            dev_ucomlpete_match_nopunc = dev_ucomlpete_nopunc
            dev_lcomplete_match_nopunc = dev_lcomplete_nopunc

            dev_ucorrect = dev_ucorr
            dev_lcorrect = dev_lcorr
            dev_ucomlpete_match = dev_ucomlpete
            dev_lcomplete_match = dev_lcomplete

            dev_root_correct = dev_root_corr

            best_epoch = epoch
            patient = 0
            # torch.save(network, model_name)
            torch.save(network.state_dict(), model_name)
            # save embedding to txt
            # FIXME format!
            #with open(model_path + 'embedding.txt', 'w') as f:
            #    for word, idx in word_alphabet.items():
            #        embedding = network.word_embedd.weight[idx, :]
            #        f.write('{}\t{}\n'.format(word, embedding))

            if test_path:
                pred_filename = model_path + 'tmp/%spred_test%d' % (str(uid), epoch)
                pred_writer.start(pred_filename)
                gold_filename = model_path + 'tmp/%sgold_test%d' % (str(uid), epoch)
                gold_writer.start(gold_filename)

                test_ucorrect = 0.0
                test_lcorrect = 0.0
                test_ucomlpete_match = 0.0
                test_lcomplete_match = 0.0
                test_total = 0

                test_ucorrect_nopunc = 0.0
                test_lcorrect_nopunc = 0.0
                test_ucomlpete_match_nopunc = 0.0
                test_lcomplete_match_nopunc = 0.0
                test_total_nopunc = 0
                test_total_inst = 0

                test_root_correct = 0.0
                test_total_root = 0
                for batch in conllx_stacked_data.iterate_batch_stacked_variable(data_test, batch_size, pos_embedding, type='dev'):
                    input_encoder, _ = batch
                    word, char, pos, heads, types, masks, lengths = input_encoder
                    heads_pred, types_pred, _, _ = network.decode(word, char, pos, mask=masks, length=lengths, beam=beam, leading_symbolic=conllx_stacked_data.NUM_SYMBOLIC_TAGS)

                    word = word.data.cpu().numpy()
                    pos = pos.data.cpu().numpy()
                    lengths = lengths.cpu().numpy()
                    heads = heads.data.cpu().numpy()
                    types = types.data.cpu().numpy()

                    pred_writer.write(word, pos, heads_pred, types_pred, lengths, symbolic_root=True)
                    gold_writer.write(word, pos, heads, types, lengths, symbolic_root=True)

                    stats, stats_nopunc, stats_root, num_inst = parser.eval(word, pos, heads_pred, types_pred, heads, types, word_alphabet, pos_alphabet, lengths, punct_set=punct_set, symbolic_root=True)
                    ucorr, lcorr, total, ucm, lcm = stats
                    ucorr_nopunc, lcorr_nopunc, total_nopunc, ucm_nopunc, lcm_nopunc = stats_nopunc
                    corr_root, total_root = stats_root

                    test_ucorrect += ucorr
                    test_lcorrect += lcorr
                    test_total += total
                    test_ucomlpete_match += ucm
                    test_lcomplete_match += lcm

                    test_ucorrect_nopunc += ucorr_nopunc
                    test_lcorrect_nopunc += lcorr_nopunc
                    test_total_nopunc += total_nopunc
                    test_ucomlpete_match_nopunc += ucm_nopunc
                    test_lcomplete_match_nopunc += lcm_nopunc

                    test_root_correct += corr_root
                    test_total_root += total_root

                    test_total_inst += num_inst

                pred_writer.close()
                gold_writer.close()
        else:
            if dev_ucorr_nopunc * 100 / dev_total_nopunc < dev_ucorrect_nopunc * 100 / dev_total_nopunc - 5 or patient >= schedule:
                # network = torch.load(model_name)
                network.load_state_dict(torch.load(model_name))
                lr = lr * decay_rate
                if use_bert:
                    # optim = generate_differentlr_bert_optimizer(lr, lr, network)
                    optim = generate_old_bert_optimizer(len(data_train) * num_epochs, lr, network)
                else:
                    optim = generate_optimizer(opt, lr, network.parameters())
                patient = 0
                decay += 1
                if decay % double_schedule_decay == 0:
                    schedule *= 2
            else:
                patient += 1

        print('----------------------------------------------------------------------------------------------------------------------------')
        print('best dev  W. Punct: ucorr: %d, lcorr: %d, total: %d, uas: %.2f%%, las: %.2f%%, ucm: %.2f%%, lcm: %.2f%% (epoch: %d)' % (
            dev_ucorrect, dev_lcorrect, dev_total, dev_ucorrect * 100 / dev_total, dev_lcorrect * 100 / dev_total,
            dev_ucomlpete_match * 100 / dev_total_inst, dev_lcomplete_match * 100 / dev_total_inst,
            best_epoch))
        print('best dev  Wo Punct: ucorr: %d, lcorr: %d, total: %d, uas: %.2f%%, las: %.2f%%, ucm: %.2f%%, lcm: %.2f%% (epoch: %d)' % (
            dev_ucorrect_nopunc, dev_lcorrect_nopunc, dev_total_nopunc,
            dev_ucorrect_nopunc * 100 / dev_total_nopunc, dev_lcorrect_nopunc * 100 / dev_total_nopunc,
            dev_ucomlpete_match_nopunc * 100 / dev_total_inst, dev_lcomplete_match_nopunc * 100 / dev_total_inst,
            best_epoch))
        print('best dev  Root: corr: %d, total: %d, acc: %.2f%% (epoch: %d)' % (dev_root_correct, dev_total_root, dev_root_correct * 100 / dev_total_root, best_epoch))
        print('----------------------------------------------------------------------------------------------------------------------------')
        if test_path:
            print('best test W. Punct: ucorr: %d, lcorr: %d, total: %d, uas: %.2f%%, las: %.2f%%, ucm: %.2f%%, lcm: %.2f%% (epoch: %d)' % (
                test_ucorrect, test_lcorrect, test_total, test_ucorrect * 100 / test_total, test_lcorrect * 100 / test_total,
                test_ucomlpete_match * 100 / test_total_inst, test_lcomplete_match * 100 / test_total_inst,
                best_epoch))
            print('best test Wo Punct: ucorr: %d, lcorr: %d, total: %d, uas: %.2f%%, las: %.2f%%, ucm: %.2f%%, lcm: %.2f%% (epoch: %d)' % (
                test_ucorrect_nopunc, test_lcorrect_nopunc, test_total_nopunc,
                test_ucorrect_nopunc * 100 / test_total_nopunc, test_lcorrect_nopunc * 100 / test_total_nopunc,
                test_ucomlpete_match_nopunc * 100 / test_total_inst, test_lcomplete_match_nopunc * 100 / test_total_inst,
                best_epoch))
            print('best test Root: corr: %d, total: %d, acc: %.2f%% (epoch: %d)' % (test_root_correct, test_total_root, test_root_correct * 100 / test_total_root, best_epoch))
            print('============================================================================================================================')

        if decay == max_decay:
            break

    def save_result():
        result_path = model_name + '.result.txt'
        best_dev_Punc = 'best dev  W. Punct: ucorr: %d, lcorr: %d, total: %d, uas: %.2f%%, las: %.2f%%, ucm: %.2f%%, lcm: %.2f%% (epoch: %d)' % (
            dev_ucorrect, dev_lcorrect, dev_total, dev_ucorrect * 100 / dev_total, dev_lcorrect * 100 / dev_total,
            dev_ucomlpete_match * 100 / dev_total_inst, dev_lcomplete_match * 100 / dev_total_inst,
            best_epoch)
        best_dev_noPunc = 'best dev  Wo Punct: ucorr: %d, lcorr: %d, total: %d, uas: %.2f%%, las: %.2f%%, ucm: %.2f%%, lcm: %.2f%% (epoch: %d)' % (
            dev_ucorrect_nopunc, dev_lcorrect_nopunc, dev_total_nopunc,
            dev_ucorrect_nopunc * 100 / dev_total_nopunc, dev_lcorrect_nopunc * 100 / dev_total_nopunc,
            dev_ucomlpete_match_nopunc * 100 / dev_total_inst, dev_lcomplete_match_nopunc * 100 / dev_total_inst,
            best_epoch)
        best_dev_Root = 'best dev  Root: corr: %d, total: %d, acc: %.2f%% (epoch: %d)' % (
            dev_root_correct, dev_total_root, dev_root_correct * 100 / dev_total_root, best_epoch)
        f = open(result_path, 'w', encoding="utf-8")
        f.write(best_dev_Punc + '\n')
        f.write(best_dev_noPunc + '\n')
        f.write(best_dev_Root)
        f.close()

    save_result()

if __name__ == '__main__':
    os.environ["CUDA_DEVICE_ORDER"]="PCI_BUS_ID"
    os.environ["CUDA_VISIBLE_DEVICES"]="0"
    main()
