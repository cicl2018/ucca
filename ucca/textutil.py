"""Utility functions for UCCA package."""
import os
import sys
import time
from collections import OrderedDict
from collections import deque
from enum import Enum
from itertools import groupby, islice
from operator import attrgetter

import numpy as np
from tqdm import tqdm

from ucca import layer0, layer1

MODEL_ENV_VAR = "SPACY_MODEL"
DEFAULT_MODEL = {"en": "en_core_web_md", "fr": "fr_core_news_md", "de": "de_core_news_sm"}


class Attr(Enum):
    ORTH = 0
    LEMMA = 1
    TAG = 2
    POS = 3
    ENT_TYPE = 4
    ENT_IOB = 5
    DEP = 6
    HEAD = 7
    SHAPE = 8
    PREFIX = 9
    SUFFIX = 10

    def __call__(self, value, vocab=None, as_array=False, lang=None):
        if value is None:
            return None
        if self in (Attr.ENT_IOB, Attr.HEAD):
            return int(np.int64(value))
        try:
            if as_array:
                if self in (Attr.ORTH, Attr.LEMMA):
                    if get_vocab(vocab, lang)[value].is_oov:
                        return None
                return int(value)
            return get_vocab(vocab, lang)[value].text
        except KeyError:
            return None
    
    @property
    def key(self):
        return self.name.lower()


def get_nlp(lang="en"):
    instance = nlp.get(lang)
    if instance is None:
        import spacy
        model_name = os.environ.get("_".join((MODEL_ENV_VAR, lang.upper()))) or os.environ.get(MODEL_ENV_VAR) or \
            DEFAULT_MODEL.get(lang, "xx")
        started = time.time()
        print("Loading spaCy model '%s'... " % model_name, end="", flush=True)
        try:
            nlp[lang] = instance = spacy.load(model_name)
        except OSError:
            spacy.cli.download(None, model_name)
            try:
                nlp[lang] = instance = spacy.load(model_name)
            except OSError as e:
                raise OSError("Failed to get spaCy model. Download it manually using "
                              "`python -m spacy download %s`." % model_name) from e
        tokenizer[lang] = instance.tokenizer
        instance.tokenizer = lambda words: spacy.tokens.Doc(instance.vocab, words=words)
        print("Done (%.3fs)." % (time.time() - started))
    return instance


nlp = {}
tokenizer = {}


def get_tokenizer(tokenized=False, lang="en"):
    instance = get_nlp(lang)
    return instance.tokenizer if tokenized else tokenizer[lang]


def get_vocab(vocab=None, lang=None):
    if vocab is not None:
        return vocab
    return (get_nlp(lang) if lang else get_nlp()).vocab


def get_word_vectors(dim=None, size=None, filename=None, as_array=False, lang="en"):
    """
    Get word vectors from spaCy model or from text file
    :param dim: dimension to trim vectors to (default: keep original)
    :param size: maximum number of vectors to load (default: all)
    :param filename: text file to load vectors from (default: from spaCy)
    :param as_array: instead of strings, keys of returned dict will be spaCy integer IDs (default: strings)
    :param lang: language to use spaCy model for (default: English)
    :return: tuple of (dict of word [string or integer] -> vector [NumPy array], dimension)
    """
    vocab = get_nlp(lang).vocab
    if filename:
        it = read_word_vectors(dim, size, filename)
        nr_row, nr_dim = next(it)
        vectors = OrderedDict(islice(tqdm(((vocab[w].orth if as_array else w, v) for w, v in it
                                           if not (as_array and vocab[w].is_oov)),
                                          desc="Loading '%s'" % filename, postfix=dict(dim=nr_dim),
                                          file=sys.stdout, total=nr_row, unit=" vectors"), nr_row))
    else:  # return spaCy vectors
        nr_row, nr_dim = vocab.vectors.shape
        # if dim is not None:  # Disabled due to explosion/spaCy#1518
        #     if dim < nr_dim:
        #         vocab.vectors.resize(shape=(int(size or nr_row), int(dim)))
        lexemes = sorted([l for l in vocab if l.has_vector], key=attrgetter("prob"), reverse=True)[:size]
        vectors = OrderedDict((l.orth if as_array else l.orth_, l.vector) for l in lexemes)
    return vectors, nr_dim


def read_word_vectors(dim, size, filename):
    """
    Read word vectors from text file, with an optional first row indicating size and dimension
    :param dim: dimension to trim vectors to
    :param size: maximum number of vectors to load
    :param filename: text file to load vectors from
    :return: generator: first element is (#vectors, #dims); and all the rest are (word [string], vector [NumPy array])
    """
    try:
        first_line = True
        nr_row = nr_dim = None
        with open(filename, encoding="utf-8") as f:
            for line in f:
                fields = line.split()
                if first_line:
                    first_line = False
                    try:
                        nr_row, nr_dim = map(int, fields)
                        is_header = True
                    except ValueError:
                        nr_dim = len(fields) - 1  # No header, just get vector length from first one
                        is_header = False
                    if dim and dim < nr_dim:
                        nr_dim = dim
                    yield size or nr_row, nr_dim
                    if is_header:
                        continue  # Read next line
                word, *vector = fields
                if len(vector) >= nr_dim:  # May not be equal if word is whitespace
                    yield word, np.asarray(vector[-nr_dim:], dtype="f")
    except OSError as e:
        raise IOError("Failed loading word vectors from '%s'" % filename) from e


def annotate(passage, replace=False, as_array=False, lang="en", verbose=False):
    """
    Run spaCy pipeline on the given passage, unless already annotated
    :param passage: Passage object, whose layer 0 nodes will be added entries in the `extra' dict
    :param replace: even if a given passage is already annotated, replace with new annotation
    :param as_array: instead of adding `extra' entries to each terminal, set layer 0 extra["doc"] to array of ids
    :param lang: optional two-letter language code
    :param verbose: whether to print annotated text
    """
    list(annotate_all([passage], verbose=verbose, replace=replace, as_array=as_array, lang=lang))


def annotate_all(passages, replace=False, as_array=False, lang="en", verbose=False):
    """
    Run spaCy pipeline on the given passages, unless already annotated
    :param passages: iterable of Passage objects, whose layer 0 nodes will be added entries in the `extra' dict
    :param replace: even if a given passage is already annotated, replace with new annotation
    :param as_array: instead of adding `extra' entries to each terminal, set layer 0 extra["doc"] to array of ids
    :param lang: optional two-letter language code, will be overridden if passage has "lang" attrib
    :param verbose: whether to print annotated text
    :return generator of annotated passages, which are actually modified in-place (same objects as input)
    """
    for passage_lang, passages_by_lang in groupby(passages, get_lang):
        for need_annotation, stream in groupby(to_annotate(passages_by_lang, replace, as_array), lambda x: bool(x[0])):
            annotated = get_nlp(passage_lang or lang).pipe(stream, as_tuples=True) if need_annotation else stream
            annotated = set_docs(annotated, as_array, passage_lang or lang, verbose)
            yield from (deque(passages, maxlen=1).pop() for passage, passages in groupby(annotated))  # get last one


def get_lang(passage):
    return passage.attrib.get("lang")


def to_annotate(passages, replace, as_array):
    return (([t.text for t in terminals] if replace or not is_annotated(passage, as_array) else (),
             (i, terminals, passage)) for passage in passages
            for i, terminals in enumerate(break2paragraphs(passage, return_terminals=True)))


def is_annotated(passage, as_array):
    l0 = passage.layer(layer0.LAYER_ID)
    if as_array:
        docs = l0.extra.get("doc")
        return docs is not None and len(docs) == max(t.paragraph for t in l0.all)
    return all(a.key in t.extra for t in l0.all for a in Attr)


def set_docs(annotated, as_array, lang, verbose):
    for doc, (i, terminals, passage) in annotated:
        if doc:  # Not empty, so copy values
            from spacy import attrs
            arr = doc.to_array([getattr(attrs, a.name) for a in Attr])
            vocab = get_nlp(lang).vocab
            if as_array:
                docs = passage.layer(layer0.LAYER_ID).extra.setdefault("doc", [[]])
                while len(docs) < i + 1:
                    docs.append([])
                docs[i] = [[a(v, vocab, as_array=True) for a, v in zip(Attr, values)] for values in arr]
            else:
                for terminal, values in zip(terminals, arr):
                    for attr, value in zip(Attr, values):
                        terminal.extra[attr.key] = attr(value, vocab)
        if verbose:
            data = [[a.key for a in Attr]] + \
                   [[str(a(t.tok[a.value], get_nlp(lang).vocab) if as_array else t.extra[a.key])
                     for a in Attr] for j, t in enumerate(terminals)]
            width = [max(len(f) for f in t) for t in data]
            for j in range(len(Attr)):
                print(" ".join("%-*s" % (w, f[j]) for f, w in zip(data, width)))
            print()
        yield passage


SENTENCE_END_MARKS = ('.', '?', '!')


def break2sentences(passage, lang="en"):
    """
    Breaks paragraphs into sentences according to the annotation.

    A sentence is a list of terminals which ends with a mark from
    SENTENCE_END_MARKS, and is also the end of a paragraph or parallel scene.
    :param passage: the Passage object to operate on
    :param lang: optional two-letter language code
    :return a list of positions in the Passage, each denotes a closing Terminal of a sentence.
    """
    l1 = passage.layer(layer1.LAYER_ID)
    terminals = extract_terminals(passage)
    if any(n.outgoing for n in l1.all):  # Passage is labeled
        ps_ends = [ps.end_position for ps in l1.top_scenes]
        ps_starts = [ps.start_position for ps in l1.top_scenes]
        marks = [t.position for t in terminals if t.text in SENTENCE_END_MARKS]
        # Annotations doesn't always include the ending period (or other mark)
        # with the parallel scene it closes. Hence, if the terminal before the
        # mark closed the parallel scene, and this mark doesn't open a scene
        # in any way (hence it probably just "hangs" there), it's a sentence end
        marks = [x for x in marks if x in ps_ends or ((x - 1) in ps_ends and x not in ps_starts)]
    else:  # Not labeled, split using spaCy
        annotated = get_nlp(lang=lang)([t.text for t in terminals])
        marks = [span.end for span in annotated.sents]
    marks = sorted(set(marks + break2paragraphs(passage)))
    # Avoid punctuation-only sentences
    if len(marks) > 1:
        marks = [x for x, y in zip(marks[:-1], marks[1:]) if not all(layer0.is_punct(t) for t in terminals[x:y])] + \
                [marks[-1]]
    return marks


def extract_terminals(p):
    """returns an iterator of the terminals of the passage p"""
    return p.layer(layer0.LAYER_ID).all


def break2paragraphs(passage, return_terminals=False):
    """
    Breaks into paragraphs according to the annotation.

    Uses the `paragraph' attribute of layer 0 to find paragraphs.
    :param passage: the Passage object to operate on
    :param return_terminals: whether to return actual Terminal objects of all terminals rather than just end positions
    :return a list of positions in the Passage, each denotes a closing Terminal of a paragraph.
    """
    terminals = list(extract_terminals(passage))
    return [list(p) for _, p in groupby(terminals, key=attrgetter("paragraph"))] if return_terminals else \
        [t.position - 1 for t in terminals if t.position > 1 and t.para_pos == 1] + [terminals[-1].position]


def indent_xml(xml_as_string):
    """
    Indents a string of XML-like objects.

    This works only for units with no text or tail members, and only for
    strings whose leaves are written as <tag /> and not <tag></tag>.
    :param xml_as_string: XML string to indent
    :return indented XML string
    """
    tabs = 0
    lines = str(xml_as_string).replace('><', '>\n<').splitlines()
    s = ''
    for line in lines:
        if line.startswith('</'):
            tabs -= 1
        s += ("  " * tabs) + line + '\n'
        if not (line.endswith('/>') or line.startswith('</')):
            tabs += 1
    return s
