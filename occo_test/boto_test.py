### Copyright 2014, MTA SZTAKI, www.sztaki.hu
###
### Licensed under the Apache License, Version 2.0 (the "License");
### you may not use this file except in compliance with the License.
### You may obtain a copy of the License at
###
###    http://www.apache.org/licenses/LICENSE-2.0
###
### Unless required by applicable law or agreed to in writing, software
### distributed under the License is distributed on an "AS IS" BASIS,
### WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
### See the License for the specific language governing permissions and
### limitations under the License.
#!/dev/null

import unittest
from nose.tools import ok_, eq_
import common
import occo.plugins.resourcehandler.ec2
from occo.resourcehandler import ResourceHandler, ResourceHandlerProvider
import occo.plugins.infraprocessor.basic_infraprocessor
import occo.infraprocessor as ip
import occo.infraprocessor.synchronization.primitives as sp
import occo.configmanager as sc
import occo.infobroker as ib
import occo.infobroker.dynamic_state_provider as dsp 
from occo.infobroker.uds import UDS
import occo.util as util
import uuid
import yaml
import logging
import os, sys

log = logging.getLogger('occo_test.ec2_test')

DROP_NODES_FILE = 'occo_test/drop_nodes.yaml'

cfg = common.configure()

real_resource = unittest.skipIf(getattr(cfg, 'skip_backend_tests', False),
                                'Omitting tests using real resources')

class BotoTest(unittest.TestCase):
    def setUp(self):
        if os.path.isfile(DROP_NODES_FILE):
            with open(DROP_NODES_FILE) as f:
                self.drop_nodes = yaml.load(f)
        else:
            self.drop_nodes = []

        self.cfg = cfg.ch_cfgs
        cleaner = util.Cleaner(hide_keys=['password'])
        log.debug(
            'Using Boto config:\n%s',
            yaml.dump(cleaner.deep_copy(self.cfg)))

    def test_full_dryrun(self):
        self.ch = ResourceHandler(self.cfg)
        with util.global_dry_run():
            nid = self.ch.create_node(cfg.node_defs['node1'])

            self.sc = sc.ConfigManager.instantiate(protocol='dummy')
            self.uds = UDS.instantiate(protocol='dict')
            mib = ib.real_main_info_broker = \
                ib.InfoRouter(sub_providers=[
                    self.uds,
                    self.sc,
                    dsp.DynamicStateProvider(self.sc, self.ch),
                    sp.SynchronizationProvider(),
                    ResourceHandlerProvider(self.ch)
                ])

            try:
                log.debug(mib.get('node.resource.state',
                                  dict(instance_id=nid,
                                       node_id="test",
                                       backend_id='lpds')))
            finally:
                self.ch.drop_node(dict(instance_id=nid,
                                       node_id="test",
                                       backend_id='lpds'))

    def update_drop_nodes(self):
        with open(DROP_NODES_FILE, 'w') as f:
            f.write(yaml.dump(self.drop_nodes))
            log.debug("Allocated nodes: %r", self.drop_nodes)

    @real_resource
    def test_create_node(self):
        self.ch = ResourceHandler(self.cfg)
        node_def = cfg.node_defs['node_lpds']
        log.debug("node_desc: %r", node_def)
        nid = self.ch.create_node(node_def)
        log.debug("Resource acquired; node_id = %r", nid)
        self.drop_nodes.append(dict(instance_id=nid, node_id="test",
                                    backend_id=node_def['backend_id']))
        self.update_drop_nodes()

    @real_resource
    def test_create_using_ip(self):
        node_def = cfg.node_defs['node_lpds']
        self.ch = ResourceHandler(self.cfg)
        self.sc = sc.ConfigManager.instantiate(protocol='dummy')
        self.uds = UDS.instantiate(protocol='dict')
        self.uds.kvstore.set_item('node_def:test', [node_def])
        mib = ib.InfoRouter(main_info_broker=True, sub_providers=[
            self.uds,
            self.sc,
            dsp.DynamicStateProvider(self.sc, self.ch),
            sp.SynchronizationProvider(),
            ResourceHandlerProvider(self.ch)
        ])

        eid = str(uuid.uuid4())
        nid = str(uuid.uuid4())
        node_desc = dict(
            infra_id=eid,
            node_id=nid,
            type='test',
            user_id=1,
            name='test')
        infrap = ip.InfraProcessor.instantiate(
            'basic', self.uds, self.ch, self.sc)
        cmd_cre = infrap.cri_create_infrastructure(eid)
        cmd_crn = infrap.cri_create_node(node_desc)
        infrap.push_instructions(cmd_cre)
        node = infrap.push_instructions(cmd_crn)[0]
        status = mib.get('node.resource.state', node)
        self.drop_nodes.append(dict(instance_id=nid, node_id="test"))

    @real_resource
    def test_drop_node(self):
        self.ch = ResourceHandler(self.cfg)
        remaining = []
        last_exception = None
        for i in self.drop_nodes:
            try:
                self.ch.drop_node(i)
            except Exception as ex:
                log.exception('Failure:')
                last_exception = ex
                remaining.append(i)
            else:
                log.debug('Successfully dropped node.')
        self.drop_nodes = remaining
        self.update_drop_nodes()
        if last_exception:
            raise last_exception.with_traceback(sys.exc_info()[2])

    @real_resource
    def test_node_status(self):
        self.ch = ResourceHandler(self.cfg)
        last_exception = None

        self.sc = sc.ConfigManager.instantiate(protocol='dummy')
        self.uds = UDS.instantiate(protocol='dict')
        mib = ib.InfoRouter(main_info_broker=True, sub_providers=[
            self.uds,
            self.sc,
            dsp.DynamicStateProvider(self.sc, self.ch),
            sp.SynchronizationProvider(),
            ResourceHandlerProvider(self.ch)
        ])

        for i in self.drop_nodes:
            try:
                node_state = mib.get('node.resource.state', i)
                log.info("Status of node %r is %r", i, node_state)
            except Exception as ex:
                log.exception('Failure:')
                last_exception = ex
        if last_exception:
            raise last_exception.with_traceback(sys.exc_info()[2])
