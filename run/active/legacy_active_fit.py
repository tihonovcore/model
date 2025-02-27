import json
import random
import re
import subprocess
import tensorflow as tf

from os import walk
from os.path import join
from random import shuffle

from memory_profiler import profile

from actions.find_possible_children import get_weights_batch
from actions.train_model import syntax_loss
from configuration import Configuration
from path_model.slm import SLM
from profiler.profiler import Profiler
from type_model.question_model import QuestionModel


class cnt_node_holder:
    cnt = 0


# @profile
def predict(request, composed, left_brothers, type_container_id, type_container_embeddings, leaf_types, root_types, index_among_brothers, slm, all_syntax_losses, all_predicted_types, all_predicted_kinds, depth=0):
    possible_children, impossible_children = get_weights_batch(composed, left_brothers)
    possible_children = possible_children[0]  # single element at batch

    _index_among_brothers = tf.constant(left_brothers.shape[0], shape=(1, ))
    reconstructed_kind, reconstructed_type = slm((composed, _index_among_brothers, type_container_id, leaf_types, root_types, type_container_embeddings))

    syntax_ls = syntax_loss(None, reconstructed_kind, impossible_children)
    all_syntax_losses.append(syntax_ls)

    reconstructed_kind = tf.reshape(reconstructed_kind, (Configuration.vocabulary_size,))  # single element at batch
    reconstructed_type = reconstructed_type[0]  # single element at batch

    # todo: make probability less
    al_probability = 1.0 * max(depth, left_brothers.shape[0])
    cnt_node_holder.cnt += 1

    if Configuration.string2integer['AFTER_LAST'] in possible_children and random.random() < al_probability:
        kind_id = Configuration.string2integer['AFTER_LAST']
    else:
        kind_id_among_possible = random.randrange(len(possible_children))
        kind_id = possible_children[kind_id_among_possible]

    kind_str = Configuration.integer2string[kind_id]
    print('%s from %d' % (kind_str, len(possible_children)))

    kind_ = reconstructed_kind[kind_id]
    all_predicted_kinds.append(kind_)

    type_id = tf.argmax(reconstructed_type).numpy()
    type_ = reconstructed_type[type_id]

    if kind_str != 'AFTER_LAST':
        all_predicted_types.append(type_)

    request.append('{ "kind": "%s", "type": %d }' % (kind_str, type_id))

    if kind_str == 'AFTER_LAST':
        return kind_id

    composed = update_paths(composed, kind_id)
    predicted_children = []

    while True:
        prediction = predict(request, composed, tf.ragged.constant([predicted_children]), type_container_id, type_container_embeddings, leaf_types, root_types, index_among_brothers, slm, all_syntax_losses, all_predicted_types, all_predicted_kinds, depth + 1)
        predicted_children.append(prediction)

        if prediction == Configuration.string2integer['AFTER_LAST']:
            break

    return kind_id


def update_paths(old_composed, new_kind):
    addition = [Configuration.string2integer['↓'], new_kind]

    def up_path(x):
        return tf.concat([x, addition], axis=0)

    def up_batch_elem(x):
        return tf.map_fn(up_path, x)

    return tf.map_fn(up_batch_elem, old_composed)


def must_be_skipped(path):
    if path[-2:] != 'kt':
        return True

    if path.endswith('kt30402.kt') or path.endswith('crossTypeEquals.kt') or path.endswith('jsNative.kt'):
        return True

    with open(path) as source:
        text = source.read()

        if re.search('//\\s*?FILE:', text) is not None:
            return True
        if re.search('//\\s*?WITH_RUNTIME', text) is not None:
            return True
        if re.search('//\\s*?FILE: .*?\\.java', text) is not None:
            return True


MIN_LG = 0.00001


def do_fit():
    file_paths = []

    if Configuration.use_leak_cheat:
        with open(Configuration.kotlin_test_directory, 'r') as tests:
            for file_path in tests.read().split("\n"):
                if must_be_skipped(file_path):
                    continue

                file_paths.append(file_path)
    else:
        for root, _, files in walk(Configuration.kotlin_test_directory):
            for file in files:
                file_path = join(root, file)
                if must_be_skipped(file_path):
                    continue

                file_paths.append(file_path)

    question_model = QuestionModel(mode=Configuration.recurrent_mode)
    question_model.trainable = False
    question_model.load_weights(Configuration.saved_type_model)
    type_embeddings = question_model.type_embeddings

    slm = SLM(batch_size=20)
    slm.load_weights(Configuration.saved_model)

    optimizer = tf.keras.optimizers.Adam(learning_rate=1e-3)

    # shuffle(file_paths)

    for file_number, file_path in enumerate(file_paths):
        if file_number % 5 == 0:
            slm.save_weights(Configuration.saved_model)

        print(file_path)

        with open(Configuration.request, 'w') as communication_file:
            communication_file.write('{ request_type: "EXTRACT_PATHS", request: "' + file_path + '" }')

        _ = subprocess.run(Configuration.bash_compiler, capture_output=True, shell=True)

        with open(Configuration.cooperative__take) as response_from_kotlin:
            status = response_from_kotlin.read()

        with tf.GradientTape() as tape:
            all_predicted_kinds = []
            all_predicted_types = []
            all_syntax_losses = []

            while status == "PATH":
                with open(Configuration.cooperative__paths, 'r') as json_paths:
                    paths_info = json.load(json_paths)
                with open(Configuration.cooperative__types, 'r') as json_types:
                    types_info = json.load(json_types)

                leaf_paths = paths_info["leafPaths"]
                root_path = paths_info["rootPath"]
                left_brothers = paths_info["leftBrothers"]
                leaf_types = paths_info["typesForLeafPaths"]
                root_types = paths_info["typesForRootPath"]
                index_among_brothers = paths_info["indexAmongBrothers"]

                class_embeddings, _, _ = type_embeddings(types_info)

                composed = leaf_paths + [root_path]
                type_container_id = [0]  # there is single container
                type_container_embeddings = [class_embeddings]
                leaf_types = [leaf_types]
                root_types = [root_types]

                composed = tf.ragged.constant([composed], dtype='float32')
                left_brothers = tf.ragged.constant([left_brothers])
                index_among_brothers = tf.constant([index_among_brothers])

                request = []
                predict(request, composed, left_brothers, type_container_id, type_container_embeddings, leaf_types, root_types, index_among_brothers, slm, all_syntax_losses, all_predicted_types, all_predicted_kinds)
                print('##########')

                with open(Configuration.request, 'w') as communication_file:
                    communication_file.write('{ request_type: "ON_PREDICT", request: "' + "\\n".join(request) + '" }')

                _ = subprocess.run(Configuration.bash_compiler, capture_output=True, shell=True)

                with open(Configuration.cooperative__take, 'r') as response_from_kotlin:
                    status = response_from_kotlin.read()

            full_syntax_loss = tf.constant(0.0)
            for ls in all_syntax_losses:
                full_syntax_loss = full_syntax_loss + ls
            full_syntax_loss = full_syntax_loss / Configuration.vocabulary_size
            # full_syntax_loss = full_syntax_loss / tf.constant(len(all_syntax_losses), dtype='float32')

            full_kind_loss = tf.constant(0.0)
            for prob in all_predicted_kinds:
                if status == "SUCC":
                    full_kind_loss = full_kind_loss - tf.math.log(prob + MIN_LG)
                elif status == "FAIL":
                    full_kind_loss = full_kind_loss - tf.math.log(1.0 - prob + MIN_LG)
            # full_kind_loss = full_kind_loss / tf.constant(len(all_predicted_kinds), dtype='float32')

            with open(Configuration.cooperative__compared_types) as type_result_file:
                type_result = type_result_file.readlines()

            full_type_loss = tf.constant(0.0)
            for prob, result in zip(all_predicted_types, type_result):
                if result == "true":
                    full_type_loss = full_type_loss - tf.math.log(prob + MIN_LG)
                elif result == "false":
                    full_type_loss = full_type_loss - tf.math.log(1.0 - prob + MIN_LG)
            # full_type_loss = full_type_loss / tf.constant(len(all_predicted_types), dtype='float32')

            print('syntax: %.4f' % full_syntax_loss.numpy())
            print('kinds : %.4f' % full_kind_loss.numpy())
            print('types : %.4f' % full_type_loss.numpy())

            full_loss = full_syntax_loss + full_kind_loss + full_type_loss

        if status == "SUCC":
            grads = tape.gradient(full_loss, slm.trainable_weights)
            optimizer.apply_gradients(zip(grads, slm.trainable_weights))

            print("last loss = %.4f" % full_loss)
        elif status == "FAIL":
            grads = tape.gradient(full_loss, slm.trainable_weights)
            optimizer.apply_gradients(zip(grads, slm.trainable_weights))

            print("last loss = %.4f" % full_loss)

        if status.startswith("ERROR"):
            print(status)
            # ignore

        slm.save_weights(Configuration.saved_model)


if __name__ == '__main__':
    do_fit()
