"""
CS224N 2016-17: Homework 3
util.py: General utility routines
Arun Chaganty <chaganty@stanford.edu>
"""
from __future__ import division
import tensorflow as tf
import sys
import time
import logging
import io
from collections import defaultdict, Counter, OrderedDict
import numpy as np
import tensorflow as tf
from numpy import array, zeros, allclose
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from os.path import join as pjoin
import os
import pickle

class Attention(object):
    def __init__(self):
        pass

    def forwards(self, hc, hq, hc_mask, hq_mask, max_context_length_placeholder, max_question_length_placeholder):
        '''combine context hidden state(hc) and question hidden state(hq) with attention
             measured similarity = hc.T * hq

             Context-to-query (C2Q) attention signifies which query words are most relevant to each P context word.
                attention_c2q = softmax(similarity)
                hq_hat = sum(attention_c2q*hq)

             Query-to-context (Q2C) attention signifies which context words have the closest similarity
                to one of the query words and are hence critical for answering the query.
                attention_q2c = softmax(similarity.T)
                hc_hat = sum(attention_q2c*hc)

             combine with β activation: β function can be an arbitrary trainable neural network
             g = β(hc, hq, hc_hat, hq_hat)
        '''
        """
        :param hc: [None, max_context_length_placeholder, d_Bi]
        :param hq: [None, max_question_length_placeholder, d_Bi]
        :param hc_mask:  [None, max_context_length_placeholder]
        :param hq_mask:  [None, max_question_length_placeholder]

        :return: [N, max_context_length_placeholder, d_com]
        """
        logging.info('-'*5 + 'attention' + '-'*5)
        logging.debug('Context representation: %s' % str(hc))
        logging.debug('Question representation: %s' % str(hq))
        d_en = hc.get_shape().as_list()[-1]

        # get similarity
        hc_aug = tf.reshape(hc, shape = [-1, max_context_length_placeholder, 1, d_en])
        hq_aug = tf.reshape(hq, shape = [-1, 1, max_question_length_placeholder, d_en])
        hc_mask_aug = tf.tile(tf.expand_dims(hc_mask, -1), [1, 1, max_question_length_placeholder]) # [N, JX] -(expend)-> [N, JX, 1] -(tile)-> [N, JX, JQ]
        hq_mask_aug = tf.tile(tf.expand_dims(hq_mask, -2), [1, max_context_length_placeholder, 1]) # [N, JQ] -(expend)-> [N, 1, JQ] -(tile)-> [N, JX, JQ]

        similarity = tf.reduce_sum(tf.multiply(hc_aug, hq_aug), axis = -1) # h * u: [N, JX, d_en] * [N, JQ, d_en] -> [N, JX, JQ]
        hq_mask_aug = hc_mask_aug & hq_mask_aug

        similarity = softmax_mask_prepro(similarity, hq_mask_aug)

        # get a_x
        attention_c2q = tf.nn.softmax(similarity, dim=-1) # softmax -> [N, JX, softmax(JQ)]

        #     use a_x to get u_a
        attention_c2q = tf.reshape(attention_c2q,
                            shape = [-1, max_context_length_placeholder, max_question_length_placeholder, 1])
        hq_aug = tf.reshape(hq_aug, shape = [-1, 1, max_question_length_placeholder, d_en])
        hq_hat = tf.reduce_sum(tf.multiply(attention_c2q, hq_aug), axis = -2)# a_x * u: [N, JX, JQ](weight) * [N, JQ, d_en] -> [N, JX, d_en]
        logging.debug('Context with attention: %s' % str(hq_hat))

        # get a_q
        attention_q2c = tf.reduce_max(similarity, axis=-1) # max -> [N, JX]
        attention_q2c = tf.nn.softmax(attention_q2c, dim=-1) # softmax -> [N, softmax(JX)]
        #     use a_q to get h_a
        attention_q2c = tf.reshape(attention_q2c, shape = [-1, max_context_length_placeholder, 1])
        hc_aug = tf.reshape(hc, shape = [-1, max_context_length_placeholder, d_en])

        hc_hat = tf.reduce_sum(tf.multiply(attention_q2c, hc_aug), axis = -2)# a_q * h: [N, JX](weight) * [N, JX, d_en] -> [N, d_en]
        assert hc_hat.get_shape().as_list() == [None, d_en]
        hc_hat = tf.tile(tf.expand_dims(hc_hat, -2), [1, max_context_length_placeholder, 1]) # [None, JX, d_en]

        return tf.concat([hc, hq_hat, hc*hq_hat, hc*hc_hat], 2)

def BiGRU_layer(inputs, masks, state_size, encoder_state_input, dropout=1.0, reuse=False):
    ''' Wrapped BiGRU_layer for reuse'''
    # 'outputs' is a tensor of shape [batch_size, max_time, cell_state_size]
    cell_fw = tf.contrib.rnn.GRUCell(state_size, reuse = reuse)
    cell_fw = tf.contrib.rnn.DropoutWrapper(cell_fw, input_keep_prob = dropout)

    cell_bw = tf.contrib.rnn.GRUCell(state_size, reuse = reuse)
    cell_bw = tf.contrib.rnn.DropoutWrapper(cell_bw, input_keep_prob = dropout)

    # defining initial state
    if encoder_state_input is not None:
        initial_state_fw, initial_state_bw = encoder_state_input
    else:
        initial_state_fw = None
        initial_state_bw = None

    sequence_length = tf.reduce_sum(tf.cast(masks, 'int32'), axis=1)
    sequence_length = tf.reshape(sequence_length, [-1,])

    # Outputs Tensor shaped: [batch_size, max_time, cell.output_size]
    (outputs_fw, outputs_bw), (final_state_fw, final_state_bw) = tf.nn.bidirectional_dynamic_rnn(
                                        cell_fw = cell_fw,\
                                        cell_bw = cell_bw,\
                                        inputs = inputs,\
                                        sequence_length = sequence_length,
                                        initial_state_fw = initial_state_fw,\
                                        initial_state_bw = initial_state_bw,
                                        dtype = tf.float32)

    outputs = tf.concat([outputs_fw, outputs_bw], 2)

    # final_state_fw and final_state_bw are the final states of the forwards/backwards LSTM
    final_state = tf.concat([final_state_fw, final_state_bw], 1)
    return (outputs, final_state, (final_state_fw, final_state_bw))

def BiLSTM_layer(inputs, masks, state_size, encoder_state_input, dropout=1.0, reuse=False):
    ''' Wrapped BiLSTM_layer for reuse'''
    # 'outputs' is a tensor of shape [batch_size, max_time, cell_state_size]
    cell_fw = tf.contrib.rnn.BasicLSTMCell(state_size, reuse = reuse)
    cell_fw = tf.contrib.rnn.DropoutWrapper(cell_fw, input_keep_prob = dropout)

    cell_bw = tf.contrib.rnn.BasicLSTMCell(state_size, reuse = reuse)
    cell_bw = tf.contrib.rnn.DropoutWrapper(cell_bw, input_keep_prob = dropout)

    # defining initial state
    if encoder_state_input is not None:
        initial_state_fw, initial_state_bw = encoder_state_input
    else:
        initial_state_fw = None
        initial_state_bw = None

    sequence_length = tf.reduce_sum(tf.cast(masks, 'int32'), axis=1)
    sequence_length = tf.reshape(sequence_length, [-1,])

    # Outputs Tensor shaped: [batch_size, max_time, cell.output_size]
    (outputs_fw, outputs_bw), (final_state_fw, final_state_bw) = tf.nn.bidirectional_dynamic_rnn(
                                        cell_fw = cell_fw,\
                                        cell_bw = cell_bw,\
                                        inputs = inputs,\
                                        sequence_length = sequence_length,
                                        initial_state_fw = initial_state_fw,\
                                        initial_state_bw = initial_state_bw,
                                        dtype = tf.float32)

    outputs = tf.concat([outputs_fw, outputs_bw], 2)
    # final_state_fw and final_state_bw are the final states of the forwards/backwards LSTM
    final_state = tf.concat([final_state_fw[1], final_state_bw[1]], 1)
    return (outputs, final_state, (final_state_fw, final_state_bw))

def save_graphs(data, path):

    # First plot the losses
    losses = data["losses"]

    fig = plt.figure()
    plt.plot([i for i in range(len(losses))], losses)
    plt.title("Batch sized used: {}".format(data["batch_size"]))
    plt.xlabel('batch number', fontsize=18)
    plt.ylabel('average loss', fontsize=16)
    fig.savefig(pjoin(path, 'loss.pdf'))
    plt.close(fig)

    batch_indices = data["batch_indices"]

    # Now plot the f1, EM for the training and validation sets
    f1_train, f1_val = data["f1_train"], data["f1_val"]

    fig = plt.figure()
    plt.plot(batch_indices, f1_train, 'b', batch_indices, f1_val, 'r')
    plt.title("Batch sized used: {}".format(data["batch_size"]))
    plt.xlabel('batch number', fontsize=18)
    plt.ylabel('F1 Score', fontsize = 16)
    fig.savefig(pjoin(path, "f1_scores.pdf"))
    plt.close(fig)

    EM_train, EM_val = data["EM_train"], data["EM_val"]

    fig = plt.figure()
    plt.plot(batch_indices, EM_train, 'b', batch_indices, EM_val, 'r')
    plt.title("Batch sized used: {}".format(data["batch_size"]))
    plt.xlabel('batch number', fontsize=18)
    plt.ylabel('EM Score', fontsize = 16)
    fig.savefig(pjoin(path, "EM_scores.pdf"))
    plt.close(fig)

def variable_summaries(var):
    """ Attach summaries to a Tensor (for TensorBoard visualization)."""
    with tf.name_scope('summaries'):
        mean = tf.reduce_mean(var)
        tf.summary.scalar('mean', mean)
        with tf.name_scope('stddev'):
            stddev = tf.sqrt(tf.reduce_mean(tf.square(var - mean)))
        tf.summary.scalar('stddev', stddev)
        tf.summary.scalar('max', tf.reduce_max(var))
        tf.summary.scalar('min', tf.reduce_min(var))
        tf.summary.histogram('histogram', var)

def get_optimizer(opt, loss, max_grad_norm, learning_rate):
    ''' With gradient clipping '''
    if opt == "adam":
        optfn = tf.train.AdamOptimizer(learning_rate = learning_rate)
    elif opt == "sgd":
        optfn = tf.train.GradientDescentOptimizer(learning_rate = learning_rate)
    else:
        assert (False)

    grads_and_vars = optfn.compute_gradients(loss)
    variables = [output[1] for output in grads_and_vars]
    gradients = [output[0] for output in grads_and_vars]

    gradients = tf.clip_by_global_norm(gradients, clip_norm = max_grad_norm)[0]
    grads_and_vars = [(gradients[i], variables[i]) for i in range(len(gradients))]
    train_op = optfn.apply_gradients(grads_and_vars)

    return train_op

def softmax_mask_prepro(logits, mask):
    ''' Make the indexes of the mask values of 1 and indexes of non mask 0
        Set huge neg number(-1e9) in padding area
    '''
    assert logits.get_shape().ndims == mask.get_shape().ndims
    # filter out the padding area as 1, the index area becomes 0
    new_mask = tf.subtract(tf.constant(1.0), tf.cast(mask, tf.float32))
    paddings_mask = tf.multiply(new_mask, tf.constant(-1e9))
    masked_logits = tf.where(mask, logits, paddings_mask)
    return masked_logits

def get_best_span(start_logits, end_logits, context_ids):
    start_sentence_logits = []
    end_sentence_logits = []
    new_start_sentence = []
    new_end_sentence = []
    for i, c_id in enumerate(context_ids):
        new_start_sentence.append(start_logits[i])
        new_end_sentence.append(end_logits[i])
        if c_id == 6: # dot id, represents the end of a sentence
            start_sentence_logits.append(new_start_sentence)
            end_sentence_logits.append(new_end_sentence)
            new_start_sentence = []
            new_end_sentence = []
    if len(new_start_sentence) > 0:
        start_sentence_logits.append(new_start_sentence)
        end_sentence_logits.append(new_end_sentence)

    # print start_sentence_logits
    # print [len(a) for a in start_sentence_logits]
    best_word_span = (0, 0)
    best_sent_idx = 0
    argmax_j1 = 0
    max_val = start_logits[0] + end_logits[0]
    for f, (ypif, yp2if) in enumerate(zip(start_sentence_logits, end_sentence_logits)):
        argmax_j1 = 0
        for j in range(len(ypif)):
            val1 = ypif[argmax_j1]
            if val1 < ypif[j]:
                val1 = ypif[j]
                argmax_j1 = j

            val2 = yp2if[j]
            if val1 + val2 > max_val:
                best_word_span = (argmax_j1, j)
                best_sent_idx = f
                max_val = val1 + val2
    len_pre = 0
    for i in range(best_sent_idx):
        len_pre += len(start_sentence_logits[i])
    # print best_sent_idx
    best_word_span = (len_pre + best_word_span[0], len_pre + best_word_span[1])
    return best_word_span, max_val

class Progbar(object):
    """
    Progbar class copied from keras (https://github.com/fchollet/keras/)
    Displays a progress bar.
    # Arguments
        target: Total number of steps expected.
        interval: Minimum visual progress update interval (in seconds).
    """

    def __init__(self, target, width=30, verbose = 1):
        self.width = width
        self.target = target
        self.sum_values = {}
        self.unique_values = []
        self.start = time.time()
        self.total_width = 0
        self.seen_so_far = 0
        self.verbose = verbose

    def update(self, current, values=None, exact=None):
        """
        Updates the progress bar.
        # Arguments
            current: Index of current step.
            values: List of tuples (name, value_for_last_step).
                The progress bar will display averages for these values.
            exact: List of tuples (name, value_for_last_step).
                The progress bar will display these values directly.
        """
        values = values or []
        exact = exact or []

        for k, v in values:
            if k not in self.sum_values:
                self.sum_values[k] = [v * (current - self.seen_so_far), current - self.seen_so_far]
                self.unique_values.append(k)
            else:
                self.sum_values[k][0] += v * (current - self.seen_so_far)
                self.sum_values[k][1] += (current - self.seen_so_far)
        for k, v in exact:
            if k not in self.sum_values:
                self.unique_values.append(k)
            self.sum_values[k] = [v, 1]
        self.seen_so_far = current

        now = time.time()
        if self.verbose == 1:
            prev_total_width = self.total_width
            sys.stdout.write("\b" * prev_total_width)
            sys.stdout.write("\r")

            numdigits = int(np.floor(np.log10(self.target))) + 1
            barstr = '%%%dd/%%%dd [' % (numdigits, numdigits)
            bar = barstr % (current, self.target)
            prog = float(current)/self.target
            prog_width = int(self.width*prog)
            if prog_width > 0:
                bar += ('='*(prog_width-1))
                if current < self.target:
                    bar += '>'
                else:
                    bar += '='
            bar += ('.'*(self.width-prog_width))
            bar += ']'
            sys.stdout.write(bar)
            self.total_width = len(bar)

            if current:
                time_per_unit = (now - self.start) / current
            else:
                time_per_unit = 0
            eta = time_per_unit*(self.target - current)
            info = ''
            if current < self.target:
                info += ' - ETA: %ds' % eta
            else:
                info += ' - %ds' % (now - self.start)
            for k in self.unique_values:
                if isinstance(self.sum_values[k], list):
                    info += ' - %s: %.4f' % (k, self.sum_values[k][0] / max(1, self.sum_values[k][1]))
                else:
                    info += ' - %s: %s' % (k, self.sum_values[k])

            self.total_width += len(info)
            if prev_total_width > self.total_width:
                info += ((prev_total_width-self.total_width) * " ")

            sys.stdout.write(info)
            sys.stdout.flush()

            if current >= self.target:
                sys.stdout.write("\n")

        if self.verbose == 2:
            if current >= self.target:
                info = '%ds' % (now - self.start)
                for k in self.unique_values:
                    info += ' - %s: %.4f' % (k, self.sum_values[k][0] / max(1, self.sum_values[k][1]))
                sys.stdout.write(info + "\n")

    def add(self, n, values=None):
        self.update(self.seen_so_far+n, values)

def read_conll(fstream):
    """
    Reads a input stream @fstream (e.g. output of `open(fname, 'r')`) in CoNLL file format.
    @returns a list of examples [(tokens), (labels)]. @tokens and @labels are lists of string.
    """
    ret = []

    current_toks, current_lbls = [], []
    for line in fstream:
        line = line.strip()
        if len(line) == 0 or line.startswith("-DOCSTART-"):
            if len(current_toks) > 0:
                assert len(current_toks) == len(current_lbls)
                ret.append((current_toks, current_lbls))
            current_toks, current_lbls = [], []
        else:
            assert "\t" in line, r"Invalid CONLL format; expected a '\t' in {}".format(line)
            tok, lbl = line.split("\t")
            current_toks.append(tok)
            current_lbls.append(lbl)
    if len(current_toks) > 0:
        assert len(current_toks) == len(current_lbls)
        ret.append((current_toks, current_lbls))
    return ret

def test_read_conll():
    input_ = [
        "EU ORG",
        "rejects    O",
        "German MISC",
        "call   O",
        "to O",
        "boycott    O",
        "British    MISC",
        "lamb   O",
        ".  O",
        "",
        "Peter  PER",
        "Blackburn  PER",
        "",
        ]
    output = [
        ("EU rejects German call to boycott British lamb .".split(), "ORG O MISC O O O MISC O O".split()),
        ("Peter Blackburn".split(), "PER PER".split())
        ]

    assert read_conll(input_) == output

def write_conll(fstream, data):
    """
    Writes to an output stream @fstream (e.g. output of `open(fname, 'r')`) in CoNLL file format.
    @data a list of examples [(tokens), (labels), (predictions)]. @tokens, @labels, @predictions are lists of string.
    """
    for cols in data:
        for row in zip(*cols):
            fstream.write("\t".join(row))
            fstream.write("\n")
        fstream.write("\n")

def test_write_conll():
    input = [
        ("EU rejects German call to boycott British lamb .".split(), "ORG O MISC O O O MISC O O".split()),
        ("Peter Blackburn".split(), "PER PER".split())
        ]
    output = """EU  ORG
            rejects O
            German  MISC
            call    O
            to  O
            boycott O
            British MISC
            lamb    O
            .   O

            Peter   PER
            Blackburn   PER

            """
    output_ = io.StringIO()
    write_conll(output_, input)
    output_ = output_.getvalue()
    assert output == output_

def load_word_vector_mapping(vocab_fstream, vector_fstream):
    """
    Load word vector mapping using @vocab_fstream, @vector_fstream.
    Assumes each line of the vocab file matches with those of the vector
    file.
    """
    ret = OrderedDict()
    for vocab, vector in zip(vocab_fstream, vector_fstream):
        vocab = vocab.strip()
        vector = vector.strip()
        ret[vocab] = array(list(map(float, vector.split())))

    return ret

def test_load_word_vector_mapping():
    vocab = """UUUNKKK
the
,
.
of
and
in""".split("\n")
    vector = """0.172414 -0.091063 0.255125 -0.837163 0.434872 -0.499848 -0.042904 -0.059642 -0.635087 -0.458795 -0.105671 0.506513 -0.105105 -0.405678 0.493365 0.408807 0.401635 -0.817805 0.626340 0.580636 -0.246996 -0.008515 -0.671140 0.301865 -0.439651 0.247694 -0.291402 0.873009 0.216212 0.145576 -0.211101 -0.352360 0.227651 -0.118416 0.371816 0.261296 0.017548 0.596692 -0.485722 -0.369530 -0.048807 0.017960 -0.040483 0.111193 0.398039 0.162765 0.408946 0.005343 -0.107523 -0.079821
-0.454847 1.002773 -1.406829 -0.016482 0.459856 -0.224457 0.093396 -0.826833 -0.530674 1.211044 -0.165133 0.174454 -1.130952 -0.612020 -0.024578 -0.168508 0.320113 0.774229 -0.360418 1.483124 -0.230922 0.301055 -0.119924 0.601642 0.694616 -0.304431 -0.414284 0.667385 0.171208 -0.334842 -0.459286 -0.534202 0.533660 -0.379468 -0.378721 -0.240499 -0.446272 0.686113 0.662359 -0.865312 0.861331 -0.627698 -0.569544 -1.228366 -0.152052 1.589123 0.081337 0.182695 -0.593022 0.438300
-0.408797 -0.109333 -0.099279 -0.857098 -0.150319 -0.456398 -0.781524 -0.059621 0.302548 0.202162 -0.319892 -0.502241 -0.014925 0.020889 1.506245 0.247530 0.385598 -0.170776 0.325960 0.267304 0.157673 0.125540 -0.971452 -0.485595 0.487857 0.284369 -0.062811 -1.334082 0.744133 0.572701 1.009871 -0.457229 0.938059 0.654805 -0.430244 -0.697683 -0.220146 0.346002 -0.388637 -0.149513 0.011248 0.818728 0.042615 -0.594237 -0.646138 0.568898 0.700328 0.290316 0.293722 0.828779
-0.583585 0.413481 -0.708189 0.168942 0.238435 0.789011 -0.566401 0.177570 -0.244441 0.328214 -0.319583 -0.468558 0.520323 0.072727 1.792047 -0.781348 -0.636644 0.070102 -0.247090 0.110990 0.182112 1.609935 -1.081378 0.922773 -0.605783 0.793724 0.476911 -1.279422 0.904010 -0.519837 1.235220 -0.149456 0.138923 0.686835 -0.733707 -0.335434 -1.865440 -0.476014 -0.140478 -0.148011 0.555169 1.356662 0.850737 -0.484898 0.341224 -0.056477 0.024663 1.141509 0.742001 0.478773
-0.811262 -1.017245 0.311680 -0.437684 0.338728 1.034527 -0.415528 -0.646984 -0.121626 0.589435 -0.977225 0.099942 -1.296171 0.022671 0.946574 0.204963 0.297055 -0.394868 0.028115 -0.021189 -0.448692 0.421286 0.156809 -0.332004 0.177866 0.074233 0.299713 0.148349 1.104055 -0.172720 0.292706 0.727035 0.847151 0.024006 -0.826570 -1.038778 -0.568059 -0.460914 -1.290872 -0.294531 0.663751 -0.646503 0.499024 -0.804777 -0.402926 -0.292201 0.348031 0.215414 0.043492 0.165281
-0.156019 0.405009 -0.370058 -1.417499 0.120639 -0.191854 -0.251213 -0.883898 -0.025010 0.150738 1.038723 0.038419 0.036411 -0.289871 0.588898 0.618994 0.087019 -0.275657 -0.105293 -0.536067 -0.181410 0.058034 0.552306 -0.389803 -0.384800 -0.470717 0.800593 -0.166609 0.702104 0.876092 0.353401 -0.314156 0.618290 0.804017 -0.925911 -1.002050 -0.231087 0.590011 -0.636952 -0.474758 0.169423 1.293482 0.609088 -0.956202 -0.013831 0.399147 0.436669 0.116759 -0.501962 1.308268
-0.008573 -0.731185 -1.108792 -0.358545 0.507277 -0.050167 0.751870 0.217678 -0.646852 -0.947062 -1.187739 0.490993 -1.500471 0.463113 1.370237 0.218072 0.213489 -0.362163 -0.758691 -0.670870 0.218470 1.641174 0.293220 0.254524 0.085781 0.464454 0.196361 -0.693989 -0.384305 -0.171888 0.045602 1.476064 0.478454 0.726961 -0.642484 -0.266562 -0.846778 0.125562 -0.787331 -0.438503 0.954193 -0.859042 -0.180915 -0.944969 -0.447460 0.036127 0.654763 0.439739 -0.038052 0.991638""".split("\n")

    wvs = load_word_vector_mapping(vocab, vector)
    assert "UUUNKKK" in wvs
    assert allclose(wvs["UUUNKKK"], array([0.172414, -0.091063, 0.255125, -0.837163, 0.434872, -0.499848, -0.042904, -0.059642, -0.635087, -0.458795, -0.105671, 0.506513, -0.105105, -0.405678, 0.493365, 0.408807, 0.401635, -0.817805, 0.626340, 0.580636, -0.246996, -0.008515, -0.671140, 0.301865, -0.439651, 0.247694, -0.291402, 0.873009, 0.216212, 0.145576, -0.211101, -0.352360, 0.227651, -0.118416, 0.371816, 0.261296, 0.017548, 0.596692, -0.485722, -0.369530, -0.048807, 0.017960, -0.040483, 0.111193, 0.398039, 0.162765, 0.408946, 0.005343, -0.107523, -0.079821]))
    assert "the" in wvs
    assert "of" in wvs
    assert "and" in wvs

def window_iterator(seq, n=1, beg="<s>", end="</s>"):
    """
    Iterates through seq by returning windows of length 2n+1
    """
    for i in range(len(seq)):
        l = max(0, i-n)
        r = min(len(seq), i+n+1)
        ret = seq[l:r]
        if i < n:
            ret = [beg,] * (n-i) + ret
        if i+n+1 > len(seq):
            ret = ret + [end,] * (i+n+1 - len(seq))
        yield ret

def test_window_iterator():
    assert list(window_iterator(list("abcd"), n=0)) == [["a",], ["b",], ["c",], ["d"]]
    assert list(window_iterator(list("abcd"), n=1)) == [["<s>","a","b"], ["a","b","c",], ["b","c","d",], ["c", "d", "</s>",]]

def one_hot(n, y):
    """
    Create a one-hot @n-dimensional vector with a 1 in position @i
    """
    if isinstance(y, int):
        ret = zeros(n)
        ret[y] = 1.0
        return ret
    elif isinstance(y, list):
        ret = zeros((len(y), n))
        ret[np.arange(len(y)),y] = 1.0
        return ret
    else:
        raise ValueError("Expected an int or list got: " + y)


def to_table(data, row_labels, column_labels, precision=2, digits=4):
    """Pretty print tables.
    Assumes @data is a 2D array and uses @row_labels and @column_labels
    to display table.
    """
    # Convert data to strings
    data = [["%04.2f"%v for v in row] for row in data]
    cell_width = max(
        max(map(len, row_labels)),
        max(map(len, column_labels)),
        max(max(map(len, row)) for row in data))
    def c(s):
        """adjust cell output"""
        return s + " " * (cell_width - len(s))
    ret = ""
    ret += "\t".join(map(c, column_labels)) + "\n"
    for l, row in zip(row_labels, data):
        ret += "\t".join(map(c, [l] + row)) + "\n"
    return ret

class ConfusionMatrix(object):
    """
    A confusion matrix stores counts of (true, guessed) labels, used to
    compute several evaluation metrics like accuracy, precision, recall
    and F1.
    """

    def __init__(self, labels, default_label=None):
        self.labels = labels
        self.default_label = default_label if default_label is not None else len(labels) -1
        self.counts = defaultdict(Counter)

    def update(self, gold, guess):
        """Update counts"""
        self.counts[gold][guess] += 1

    def as_table(self):
        """Print tables"""
        # Header
        data = [[self.counts[l][l_] for l_,_ in enumerate(self.labels)] for l,_ in enumerate(self.labels)]
        return to_table(data, self.labels, ["go\\gu"] + self.labels)

    def summary(self, quiet=False):
        """Summarize counts"""
        keys = range(len(self.labels))
        data = []
        macro = array([0., 0., 0., 0.])
        micro = array([0., 0., 0., 0.])
        default = array([0., 0., 0., 0.])
        for l in keys:
            tp = self.counts[l][l]
            fp = sum(self.counts[l_][l] for l_ in keys if l_ != l)
            tn = sum(self.counts[l_][l__] for l_ in keys if l_ != l for l__ in keys if l__ != l)
            fn = sum(self.counts[l][l_] for l_ in keys if l_ != l)

            acc = (tp + tn)/(tp + tn + fp + fn) if tp > 0  else 0
            prec = (tp)/(tp + fp) if tp > 0  else 0
            rec = (tp)/(tp + fn) if tp > 0  else 0
            f1 = 2 * prec * rec / (prec + rec) if tp > 0  else 0

            # update micro/macro averages
            micro += array([tp, fp, tn, fn])
            macro += array([acc, prec, rec, f1])
            if l != self.default_label: # Count count for everything that is not the default label!
                default += array([tp, fp, tn, fn])

            data.append([acc, prec, rec, f1])

        # micro average
        tp, fp, tn, fn = micro
        acc = (tp + tn)/(tp + tn + fp + fn) if tp > 0  else 0
        prec = (tp)/(tp + fp) if tp > 0  else 0
        rec = (tp)/(tp + fn) if tp > 0  else 0
        f1 = 2 * prec * rec / (prec + rec) if tp > 0  else 0
        data.append([acc, prec, rec, f1])
        # Macro average
        data.append(macro / len(keys))

        # default average
        tp, fp, tn, fn = default
        acc = (tp + tn)/(tp + tn + fp + fn) if tp > 0  else 0
        prec = (tp)/(tp + fp) if tp > 0  else 0
        rec = (tp)/(tp + fn) if tp > 0  else 0
        f1 = 2 * prec * rec / (prec + rec) if tp > 0  else 0
        data.append([acc, prec, rec, f1])

        # Macro and micro average.
        return to_table(data, self.labels + ["micro","macro","not-O"], ["label", "acc", "prec", "rec", "f1"])

def get_minibatches(data, minibatch_size, shuffle=True):
    """
    Iterates through the provided data one minibatch at at time. You can use this function to
    iterate through data in minibatches as follows:

        for inputs_minibatch in get_minibatches(inputs, minibatch_size):
            ...

    Or with multiple data sources:

        for inputs_minibatch, labels_minibatch in get_minibatches([inputs, labels], minibatch_size):
            ...

    Args:
        data: there are two possible values:
            - a list or numpy array
            - a list where each element is either a list or numpy array
        minibatch_size: the maximum number of items in a minibatch
        shuffle: whether to randomize the order of returned data
    Returns:
        minibatches: the return value depends on data:
            - If data is a list/array it yields the next minibatch of data.
            - If data a list of lists/arrays it returns the next minibatch of each element in the
              list. This can be used to iterate through multiple data sources
              (e.g., features and labels) at the same time.

    """
    list_data = type(data) is list and (type(data[0]) is list or type(data[0]) is np.ndarray)
    data_size = len(data[0]) if list_data else len(data)
    indices = np.arange(data_size)
    if shuffle:
        np.random.shuffle(indices)
    for minibatch_start in np.arange(0, data_size, minibatch_size):
        minibatch_indices = indices[minibatch_start:minibatch_start + minibatch_size]
        yield [minibatch(d, minibatch_indices) for d in data] if list_data \
            else minibatch(data, minibatch_indices)

def get_minibatches_with_window(data, batch_size, window_batch):
    list_data = type(data) is list and (type(data[0]) is list or type(data[0]) is np.ndarray)
    data_size = len(data[0]) if list_data else len(data)
    batch_num = int(np.ceil(data_size * 1.0 / batch_size))
    window_size = min([batch_size*window_batch, data_size])
    window_start = np.random.randint(data_size-window_size+1, size=(batch_num,))
    # print(window_start)
    for i in range(batch_num):
        window_index = np.arange(window_start[i], window_start[i]+window_size)
        # print(window_index)
        minibatch_indices = np.random.choice(window_index,size = (batch_size,),replace=False)
        # print(minibatch_indices)
        yield [minibatch(d, minibatch_indices) for d in data] if list_data \
            else minibatch(data, minibatch_indices)


def minibatch(data, minibatch_idx):
    return data[minibatch_idx] if type(data) is np.ndarray else [data[i] for i in minibatch_idx]

def minibatches(data, batch_size, shuffle=True, window_batch=None):
    batches = [np.array(col) for col in zip(*data)]
    if window_batch is None:
        return get_minibatches(batches, batch_size, shuffle)
    else:
        return get_minibatches_with_window(batches, batch_size, window_batch)


def print_sentence(output, sentence, labels, predictions):

    spacings = [max(len(sentence[i]), len(labels[i]), len(predictions[i])) for i in range(len(sentence))]
    # Compute the word spacing
    output.write("x : ")
    for token, spacing in zip(sentence, spacings):
        output.write(token)
        output.write(" " * (spacing - len(token) + 1))
    output.write("\n")

    output.write("y*: ")
    for token, spacing in zip(labels, spacings):
        output.write(token)
        output.write(" " * (spacing - len(token) + 1))
    output.write("\n")

    output.write("y': ")
    for token, spacing in zip(predictions, spacings):
        output.write(token)
        output.write(" " * (spacing - len(token) + 1))
    output.write("\n")
