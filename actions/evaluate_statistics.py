import json

import tensorflow as tf

from configuration import Configuration
from numpy import argmax


def parent_id_to_children_ids(parent_id, integer2string):
    with open(Configuration.parent_child_json, 'r') as file:
        parent2child = json.loads(file.read())

    string2integer = {}
    for (i, s) in integer2string.items():
        string2integer[s] = i

    parent_string = integer2string[parent_id]
    children_strings = parent2child[parent_string]

    return list(map(lambda child: string2integer[child], children_strings))


def evaluate_statistics(evaluate_begin, evaluate_end, composed, target_indices, targets, slm, index2word):
    real = []

    actual = []
    actual_top_5 = []

    gram_acc_1 = []
    gram_acc_5 = []

    batch_size = Configuration.test_batch_size
    for begin in range(evaluate_begin, evaluate_end, batch_size):
        if begin % 100 == 0:
            print('begin: %d' % begin)

        composed_batch = tf.ragged.constant(composed[begin:begin + batch_size])
        indices_batch = tf.constant(target_indices[begin:begin + batch_size])

        result = slm.call((composed_batch, indices_batch))

        for (res, cmp) in zip(result, composed_batch):
            parent_id = cmp[-1][-1].numpy()
            children_ids = parent_id_to_children_ids(parent_id, index2word)

            _, predicted = tf.nn.top_k(tf.gather(res, children_ids), k=1)
            gram_acc_1.append(children_ids[predicted.numpy()[0]])

            _, predicted = tf.nn.top_k(tf.gather(res, children_ids), k=min(5, len(children_ids)))
            gram_acc_5.append(list(map(lambda pred: children_ids[pred], predicted.numpy())))

        _, top_5_indices = tf.nn.top_k(result, 5)
        actual_top_5.extend(top_5_indices)

        actual_batch = tf.argmax(result, axis=1).numpy()
        real_batch = tf.argmax(targets[begin:begin + batch_size], axis=1).numpy()

        actual.extend(actual_batch)
        real.extend(real_batch)

    ok = 0
    for (a, r) in list(zip(actual, real)):
        if a == r:
            ok += 1

    print('test accuracy@1: %f' % (ok / (evaluate_end - evaluate_begin)))

    ok = 0
    for (a, r) in list(zip(actual_top_5, real)):
        if r in a:
            ok += 1

    print('test accuracy@5: %f' % (ok / (evaluate_end - evaluate_begin)))

    ok = 0
    for (g, r) in list(zip(gram_acc_1, real)):
        if r == g:
            ok += 1

    print('test naive_grammar_accuracy@1:   %f' % (ok / (evaluate_end - evaluate_begin)))

    ok = 0
    for (g, r) in list(zip(gram_acc_5, real)):
        if r in g:
            ok += 1

    print('test naive_grammar_accuracy@<=5: %f' % (ok / (evaluate_end - evaluate_begin)))

    real_stat = {}
    for target in targets[evaluate_begin:evaluate_end]:
        t = argmax(target)
        real_stat.setdefault(t, 0)
        real_stat[t] += 1

    actual_tp_stat = {}
    actual_fp_stat = {}
    for (r, a) in zip(real, actual):
        if r == a:
            actual_tp_stat.setdefault(r, 0)
            actual_tp_stat[r] += 1
        else:
            actual_fp_stat.setdefault(r, {})
            actual_fp_stat[r].setdefault(a, 0)
            actual_fp_stat[r][a] += 1

    print("TRUE POSITIVE STAT")
    for (real_index, real_count) in real_stat.items():
        actual_tp_stat.setdefault(real_index, 0)

        actual_count = actual_tp_stat[real_index]

        print('%d / %d\t= %f \t %s' % (actual_count, real_count, actual_count / real_count, index2word[real_index]))

    print("\n\n\nFALSE POSITIVE STAT")
    for (real_index, real_count) in real_stat.items():
        actual_fp_stat.setdefault(real_index, {})
        stat = actual_fp_stat[real_index]

        if len(stat) == 0:
            continue

        for index, freq in sorted(stat.items(), key=lambda s: s[1], reverse=True)[:3]:
            print('%d \t %s --> %s' % (freq, index2word[real_index], index2word[index]))
