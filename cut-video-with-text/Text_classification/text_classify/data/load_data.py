#!/usr/bin/env python  
# -*- coding: utf-8 -*-  
# @Time    : 2018/6/6 下午8:15  
# @Author  : Kaiyu  
# @Site    :   
# @File    : load_data.py

import re
import sys
import os
import numpy as np
import torch
from gensim.models import FastText, Word2Vec
from torch.autograd import Variable
import jieba
import thulac


class DataLoader(object):
    print("start loading word related model")
    thu0 = thulac.thulac()
    _big_word2vec_path = os.path.join(os.path.dirname(__file__), 'word_embed/wiki.zh/wiki.zh.bin')
    _small_word2vec_path = os.path.join(os.path.dirname(__file__), "word_embed/fasttext-skim-clean-2.pt")
    big_word2vec_model = FastText.load_fasttext_format(_big_word2vec_path)
    #big_word2vec_model = nlp.embedding.create("fasttext", source="wiki.zh")
    small_word2vec_model = Word2Vec.load(_small_word2vec_path)
#    big_word2vec_model = nlp.embedding.create("fasttext", source="wiki")
    print("loaded word related model")

    def __init__(self, src_sents, label, max_len=16, embed_dim=301, cuda=False, batch_size=64, shuffle=True,
                 evaluation=False,
                 clean=True, big_word2vec_model=None, small_word2vec_model=None, PAD='*', pad=True):
        self._cuda = cuda
        self.sents_size = len(src_sents)
        self._step = 0
        self._batch_size = batch_size
        self._stop_step = self.sents_size // self._batch_size + 1
        self._evaluation = evaluation
        self._clean = clean
        self._thu0 = ''
        if self._clean:
            self._thu0 = DataLoader.thu0
        else:
            del DataLoader.thu0
            DataLoader.thu0 = None
            self._thu0 = None
        self._max_len = max_len
        self._src_sents = np.asarray(src_sents)
        self._label = np.asarray(label, dtype=np.int64)
        self._embed_dim = embed_dim
        self._PAD = PAD
        self._pad = pad

        # loading word2vec model
        if big_word2vec_model is not None:
            self.big_word2vec_model = big_word2vec_model
        else:
            self.big_word2vec_model = DataLoader.big_word2vec_model
        if small_word2vec_model is not None:
            self.small_word2vec_model = small_word2vec_model
        else:
            self.small_word2vec_model = DataLoader.small_word2vec_model

        self._process = DataProcess(big_word2vec_model=self.big_word2vec_model,
                                    small_word2vec_model=self.small_word2vec_model,
                                    cut_model=self._thu0, max_length=self._max_len, PAD=self._PAD,
                                    evaluation=self._evaluation, clean=self._clean)
        if shuffle:
            self._shuffle()
        tmp = np.bincount(self._label)/self._label.shape[0]
        self._weights = (np.ones((tmp.shape[0])) - tmp).astype(np.float32)

    def get_weight(self):
        return self._weights

    def _shuffle(self):
        indices = np.arange(self._src_sents.shape[0])
        np.random.shuffle(indices)
        self._src_sents = self._src_sents[indices]
        self._label = self._label[indices]

    def __iter__(self):
        return self

    def __len__(self):
        return self._batch_size

    def __next__(self):
        if self._step == self._stop_step:
            self._step = 0
            raise StopIteration

        _start = self._step * self._batch_size
        _bsz = min(self._batch_size, self.sents_size - _start)
        self._step += 1
        if self._pad:
            data = self._process.pad_to_longest(self._src_sents[_start:_start + _bsz])
        else:
            data = self._src_sents[_start:_start + _bsz]
        data = self._process.convert_to_vectors_plus(data, big_sequence_length=14, embed_dim=self._embed_dim)
        label = Variable(torch.from_numpy(self._label[_start:_start + _bsz]),
                         volatile=self._evaluation)
        if self._cuda:
            # print(self._cuda)
            label = label.cuda()
            data = data.cuda()

        return data, label


class DataProcess(object):
    def __init__(self, big_word2vec_model=DataLoader.big_word2vec_model,
                 small_word2vec_model=DataLoader.small_word2vec_model,
                 max_length=16, cut_model=DataLoader.thu0, PAD='*',clean=True, evaluation=False):
        self._big_word2vec_model = big_word2vec_model
        self._small_word2vec_model = small_word2vec_model
        self._cut_model = cut_model
        self._clean = clean
        self._max_length = max_length
        self._PAD = PAD
        self._evaluation = evaluation
        if self._clean:
            self._thu0 = cut_model
        # None denote there is no word
        self._part_of_speech_index = [None, 'x', 'n', 'np', 'ns', 'ni', 'nz', 'm', 'q', 'mq', 't', 'f', 's', 'v', 'a',
                                      'd', 'h', 'k', 'i', 'j', 'r', 'c', 'p', 'u', 'y', 'e', 'o', 'g', 'w', 'id']
        self._part_of_speech_index_length = len(self._part_of_speech_index)
        pass

    def clean(self, text, thu0, stop_chars=''',?.!！;:"(){}[]，。？-；'：（）【】 ．—~'''):
        text = re.sub('[a-zA-Z0-9]+', '', text)
        for c in stop_chars:
            text = text.replace(c, ' ')

        try:
            text = thu0.fast_cut(text)
            text = [(item[0], item[1]) for item in text if item[1] != '']
        except Exception as e:
            print("thulac error {}".format(text))
            print(e)
        return text

    def pad_to_longest(self, insts):
        re = []
        for index, inst in enumerate(insts):
            if len(inst) < self._max_length:
                inst_ = inst + [(self._PAD, 0)] * (self._max_length - len(inst))
            else:
                inst_ = inst[:self._max_length]
            re.append(inst_)
        return np.asarray(re)

    def _convert_to_vectors(self, insts, length, embed_dim, model=''):
        insts_num = len(insts)
        data = np.zeros((insts_num, length, embed_dim))
        for index_0, inst in enumerate(insts):
            for index_1, word in enumerate(inst[:length]):
                if word[0] != self._PAD and word[0] in model:
                    try:
                        if index_1 > length - 1:
                            break
                        # cha_ = [0 for _ in range(self._part_of_speech_index_length)]
                        # cha_[int(word[1])] = 1
                        data[index_0][index_1] = np.append(model[word[0]], self._part_of_speech_index.index(word[1]))
                    except Exception as e:
                        print(e)
        data = data.astype(np.float32)
        data = Variable(torch.from_numpy(data), volatile=self._evaluation)
        return data

    def convert_to_vectors(self, insts, emded_dim, model):
        return self._convert_to_vectors(insts, self._max_length, embed_dim=emded_dim, model=model)

    def convert_to_vectors_plus(self, insts, embed_dim):
        big_word_vecs = self._convert_to_vectors(insts, self._max_length, embed_dim=embed_dim,
                                                 model=self._big_word2vec_model)
        small_word_vecs = self._convert_to_vectors(insts, self._max_length, embed_dim=embed_dim,
                                                   model=self._small_word2vec_model)
        data = big_word_vecs + small_word_vecs
        return data

    def convert_to_vectors_concate(self, insts, big_sequence_length, embed_dim):
        if big_sequence_length > self._max_length:
            sys.exit(1)
        small_sequence_length = self._max_length - big_sequence_length
        big_word_vecs = self._convert_to_vectors(insts, big_sequence_length, embed_dim=embed_dim,
                                                 model=self._big_word2vec_model)
        small_word_vecs = self._convert_to_vectors(insts, small_sequence_length, embed_dim=embed_dim,
                                                   model=self._small_word2vec_model)
        data = np.concatenate((big_word_vecs, small_word_vecs), axis=1)
        data = Variable(torch.from_numpy(data), volatile=self._evaluation)
        return data


if __name__ == '__main__':
    # path = '/Users/harry/PycharmProjects/toys/Text-classification/text-classify/okoo-match'
    # js_data = readfile(path)
    # with open('', 'w') as f:
    #     json.dump({'all': js_data}, f, ensure_ascii=False, indent=4, separators=(',', ': '))

    pass

    # sentence = load_sentence_data('okoo-label.json')
