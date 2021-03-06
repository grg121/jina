import os
import time
from sys import platform

import pytest
import numpy as np

from jina.flow import Flow
from jina.helper import random_name
from jina.checker import NetworkChecker
from jina.parser import set_pea_parser, set_ping_parser
from jina.proto import jina_pb2, uid
from jina.drivers.helper import array2pb
from jina.peapods.container import ContainerPea
from jina.peapods.pea import BasePea

cur_dir = os.path.dirname(os.path.abspath(__file__))

img_name = 'jina/mwu-encoder'

defaulthost = '0.0.0.0'
localhost = defaulthost if (platform == "linux" or platform == "linux2") else 'host.docker.internal'


def random_docs(num_docs, chunks_per_doc=5, embed_dim=10, jitter=1):
    c_id = 3 * num_docs  # avoid collision with docs
    for j in range(num_docs):
        d = jina_pb2.Document()
        d.tags['id'] = j
        d.text = b'hello world'
        d.embedding.CopyFrom(array2pb(np.random.random([embed_dim + np.random.randint(0, jitter)])))
        d.id = uid.new_doc_id(d)
        for k in range(chunks_per_doc):
            c = d.chunks.add()
            c.text = 'i\'m chunk %d from doc %d' % (c_id, j)
            c.embedding.CopyFrom(array2pb(np.random.random([embed_dim + np.random.randint(0, jitter)])))
            c.tags['id'] = c_id
            c.tags['parent_id'] = j
            c_id += 1
            c.parent_id = d.id
            c.id = uid.new_doc_id(c)
        yield d


@pytest.fixture(scope='module')
def docker_image_built():
    import docker
    client = docker.from_env()
    client.images.build(path=os.path.join(cur_dir, '../mwu-encoder/'), tag=img_name)
    client.close()
    yield
    time.sleep(2)
    client = docker.from_env()
    client.containers.prune()


def test_simple_container(docker_image_built):
    args = set_pea_parser().parse_args(['--uses', img_name])

    with ContainerPea(args):
        pass

    time.sleep(2)
    ContainerPea(args).start().close()


def test_simple_container_with_ext_yaml(docker_image_built):
    args = set_pea_parser().parse_args(['--uses', img_name,
                                        '--uses-internal',
                                        os.path.join(cur_dir, '../mwu-encoder/mwu_encoder_ext.yml')])

    with ContainerPea(args):
        time.sleep(2)


def test_flow_with_one_container_pod(docker_image_built):
    f = (Flow()
         .add(name='dummyEncoder1', uses=img_name))

    with f:
        f.index(input_fn=random_docs(10))


def test_flow_with_one_container_ext_yaml(docker_image_built):
    f = (Flow()
         .add(name='dummyEncoder2', uses=img_name,
              uses_internal=os.path.join(cur_dir, '../mwu-encoder/mwu_encoder_ext.yml')))

    with f:
        f.index(input_fn=random_docs(10))


def test_flow_with_replica_container_ext_yaml(docker_image_built):
    f = (Flow()
         .add(name='dummyEncoder3',
              uses=img_name,
              uses_internal=os.path.join(cur_dir, '../mwu-encoder/mwu_encoder_ext.yml'),
              parallel=3))

    with f:
        f.index(input_fn=random_docs(10))
        f.index(input_fn=random_docs(10))
        f.index(input_fn=random_docs(10))


def test_flow_topo1(docker_image_built):
    f = (Flow()
         .add(name='d0', uses='jinaai/jina:test-pip', uses_internal='_logforward', entrypoint='jina pod')
         .add(name='d1', uses='jinaai/jina:test-pip', uses_internal='_logforward', entrypoint='jina pod')
         .add(name='d2', uses='jinaai/jina:test-pip', uses_internal='_logforward',
              needs='d0', entrypoint='jina pod')
         .join(['d2', 'd1']))

    with f:
        f.index(input_fn=random_docs(10))


def test_flow_topo_mixed(docker_image_built):
    f = (Flow()
         .add(name='d4', uses='jinaai/jina:test-pip', uses_internal='_logforward', entrypoint='jina pod')
         .add(name='d5', uses='_logforward')
         .add(name='d6', uses='jinaai/jina:test-pip', uses_internal='_logforward',
              needs='d4', entrypoint='jina pod')
         .join(['d6', 'd5']))

    with f:
        f.index(input_fn=random_docs(10))


def test_flow_topo_parallel(docker_image_built):
    f = (Flow()
         .add(name='d7', uses='jinaai/jina:test-pip', entrypoint='jina pod', uses_internal='_pass', parallel=3)
         .add(name='d8', uses='_pass', parallel=3)
         .add(name='d9', uses='jinaai/jina:test-pip', entrypoint='jina pod', uses_internal='_pass',
              needs='d7')
         .join(['d9', 'd8']))

    with f:
        f.dry_run()
        f.index(input_fn=random_docs(1000))


def test_container_volume(docker_image_built, tmpdir):
    abc_path = os.path.join(tmpdir, 'abc')
    f = (Flow()
         .add(name=random_name(), uses=img_name, volumes=abc_path,
              uses_internal=os.path.join(cur_dir, '../mwu-encoder/mwu_encoder_upd.yml')))

    with f:
        f.index(random_docs(10))

    out_file = os.path.join(abc_path, 'ext-mwu-encoder.bin')

    assert os.path.exists(out_file)


def test_container_ping(docker_image_built):
    a4 = set_pea_parser().parse_args(['--uses', img_name])
    a5 = set_ping_parser().parse_args(['0.0.0.0', str(a4.port_ctrl), '--print-response'])

    # test with container
    with pytest.raises(SystemExit) as cm:
        with BasePea(a4):
            NetworkChecker(a5)

    assert cm.value.code == 0


def test_tail_host_docker2local_parallel(docker_image_built):
    f = (Flow()
         .add(name='d10', uses='jinaai/jina:test-pip', entrypoint='jina pod', uses_internal='_pass', parallel=3)
         .add(name='d11', uses='_pass'))
    with f:
        assert getattr(f._pod_nodes['d10'].peas_args['tail'], 'host_out') == defaulthost
        f.dry_run()


def test_tail_host_docker2local(docker_image_built):
    f = (Flow()
         .add(name='d12', uses='jinaai/jina:test-pip', entrypoint='jina pod', uses_internal='_pass')
         .add(name='d13', uses='_pass'))
    with f:
        assert getattr(f._pod_nodes['d12'].tail_args, 'host_out') == localhost
        f.dry_run()
