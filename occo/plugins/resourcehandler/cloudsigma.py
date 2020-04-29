### Copyright 2016, MTA SZTAKI, www.sztaki.hu
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

""" CloudSigma implementation of the
:class:`~occo.resourcehandler.resourcehandler.ResourceHandler` class.

.. moduleauthor:: Zoltan Farkas <zoltan.farkas@sztaki.mta.hu>
"""


from urllib.parse import urlparse
import occo.util.factory as factory
from occo.util import wet_method, coalesce, unique_vmname
from occo.resourcehandler import ResourceHandler, Command, RHSchemaChecker
import itertools as it
import logging
import occo.constants.status as status
import requests, json, uuid, time, base64
from occo.exceptions import SchemaError, NodeCreationError
import http.client

__all__ = ['CloudSigmaResourceHandler']

PROTOCOL_ID='cloudsigma'
STATE_MAPPING = {
    'stopping'      : status.SHUTDOWN,
    'stopped'       : status.SHUTDOWN,
    'running'       : status.READY,
    'paused'        : status.PENDING,
    'starting'      : status.PENDING,
    'unavailable'   : status.TMP_FAIL,
    'unknown'       : status.TMP_FAIL
}
log = logging.getLogger('occo.resourcehandler.cloudsigma')

wait_time_between_api_call_retries=6
max_number_of_api_call_retries=50

def get_auth(auth_data):
    return (auth_data['email'], auth_data['password'])

def get_server_json(resource_handler, srv_id):
    if not srv_id:
       return None
    r = requests.get(resource_handler.endpoint + '/servers/' + srv_id + '/',
        auth=get_auth(resource_handler.auth_data))
    if r.status_code != 200:
        log.error('[%s] Failed to get info from server %s! HTTP response code/message: %d/%s. Server response: %s.',
                  resource_handler.name, srv_id, r.status_code,
                  http.client.responses.get(r.status_code,"(undefined http code returned by CloudSigma API)"), r.text)
        return None
    return r.json()

def get_server_status(resource_handler, srv_id):
    json_data = get_server_json(resource_handler, srv_id)
    if json_data is not None and json_data.get('status'):
      srv_st = json_data['status']
    else:
      srv_st = 'unknown'
    return srv_st

class CreateNode(Command):
    def __init__(self, resolved_node_definition):
        Command.__init__(self)
        self.resolved_node_definition = resolved_node_definition

    @wet_method(["uuid123",""])
    def _clone_drive(self, resource_handler, libdrive_id):
        r = requests.post(resource_handler.endpoint + '/libdrives/' + libdrive_id + '/action/',
            auth=get_auth(resource_handler.auth_data), params={'do': 'clone'})
        retry=max_number_of_api_call_retries
        while r.status_code != 202 and retry>0:
          error_msg = '[{0}] Cloning library drive {1} failed! HTTP response code/message: {2}/{3}. Server response: {4}.'.format(resource_handler.name, libdrive_id, r.status_code,
             http.client.responses.get(r.status_code,"(undefined http code returned by CloudSigma API)"), r.text)
          log.debug(error_msg)
          log.debug('Retrying in {0} seconds...  {1} attempts left.'.format(wait_time_between_api_call_retries,retry))
          time.sleep(wait_time_between_api_call_retries)
          retry-=1
          r = requests.post(resource_handler.endpoint + '/libdrives/' + libdrive_id + '/action/',
            auth=get_auth(resource_handler.auth_data), params={'do': 'clone'})
        if r.status_code != 202:
            error_msg = '[{0}] Cloning library drive {1} failed! HTTP response code/message: {2}/{3}. Server response: {4}.'.format(
                        resource_handler.name, libdrive_id, r.status_code,
                        http.client.responses.get(r.status_code,"(undefined http code returned by CloudSigma API)"), r.text)
            return None, error_msg
        json_data = json.loads(r.text)
        uuid = json_data['objects'][0]['uuid']
        if uuid == None:
            error_msg = '[{0}] Cloning library drive {1} failed: did not receive UUID!'.format(
                        resource_handler.name, libdrive_id)
            return None, error_msg
        return uuid, ""

    @wet_method()
    def _delete_drive(self, resource_handler, drv_id):
        r = requests.delete(resource_handler.endpoint + '/drives/' + str(drv_id) + '/',
            auth=get_auth(resource_handler.auth_data))
        retry=max_number_of_api_call_retries
        while r.status_code != 204 and retry>0:
          error_msg = '[{0}] Deleting cloned drive {1} failed! HTTP response code/message: {2}/{3}. Server response: {4}.'.format(resource_handler.name, drv_id, r.status_code,
             http.client.responses.get(r.status_code,"(undefined http code returned by CloudSigma API)"), r.text)
          log.debug(error_msg)
          log.debug('Retrying in {0} seconds...  {1} attempts left.'.format(wait_time_between_api_call_retries,retry))
          time.sleep(wait_time_between_api_call_retries)
          retry-=1
          r = requests.delete(resource_handler.endpoint + '/drives/' + str(drv_id) + '/',
              auth=get_auth(resource_handler.auth_data))
        if r.status_code != 204:
            error_msg = '[{0}] Deleting cloned drive {1} failed! HTTP response code/message: {2}/{3}. Server response: {4}.'.format(
                        resource_handler.name, drv_id, r.status_code,
                        http.client.responses.get(r.status_code,"(undefined http code returned by CloudSigma API)"), r.text)
            return error_msg
        return None


    @wet_method(['unmounted',""])
    def _get_drive_status(self, resource_handler, drv_id):
        r = requests.get(resource_handler.endpoint + '/drives/' + str(drv_id) + '/',
            auth=get_auth(resource_handler.auth_data))
        retry=max_number_of_api_call_retries
        while r.status_code != 200 and retry>0:
          error_msg = '[{0}] Failed to query status of drive {1}! HTTP response code/message: {2}/{3}. Server response: {4}.'.format(resource_handler.name, drv_id, r.status_code,
                http.client.responses.get(r.status_code,"(undefined http code returned by CloudSigma API)"), r.text)
          log.debug(error_msg)
          log.debug('Retrying in {0} seconds...  {1} attempts left.'.format(wait_time_between_api_call_retries,retry))
          time.sleep(wait_time_between_api_call_retries)
          retry-=1
          r = requests.get(resource_handler.endpoint + '/drives/' + str(drv_id) + '/',
              auth=get_auth(resource_handler.auth_data))
        if r.status_code != 200:
            error_msg = '[{0}] Failed to query status of drive {1}! HTTP response code/message: {2}/{3}. Server response: {4}.'.format(
                        resource_handler.name, drv_id, r.status_code,
                        http.client.responses.get(r.status_code,"(undefined http code returned by CloudSigma API)"), r.text)
            return 'unknown', error_msg
        st = r.json()['status']
        log.debug('[%s] Status of drive %s is: %s', resource_handler.name, drv_id, st)
        return st, ""

    @wet_method([1,""])
    def _create_server(self, resource_handler, drv_id):
        """
        Start the VM instance.

        :Remark: This is a "wet method", the VM will not be started
            if the instance is in debug mode (``dry_run``).
        """
        descr = self.resolved_node_definition['resource']['description']
        context = self.resolved_node_definition.get('context', None)
        if context is not None:
            descr['meta'] = {
                'base64_fields': 'cloudinit-user-data',
                'cloudinit-user-data': base64.b64encode(context.encode('utf-8')).decode('utf-8')
            }
        if 'vnc_password' not in descr:
            descr['vnc_password'] = self.resolved_node_definition.get('node_id', "occopus")
        if 'name' not in descr:
            descr['name'] = unique_vmname(self.resolved_node_definition)
        if 'drivers' not in descr:
            descr['drives'] = []
        nd = {
            "boot_order": 1,
            "dev_channel": "0:0",
            "device": "virtio",
            "drive": str(drv_id)
        }
        descr['drives'].append(nd)
        json_data = {}
        json_data['objects'] = [descr]
        r = requests.post(resource_handler.endpoint + '/servers/',
            auth=get_auth(resource_handler.auth_data), json=json_data)
        retry=max_number_of_api_call_retries
        while r.status_code not in [201] and retry>0:
          error_msg = '[{0}] Failed to create server! HTTP response code/message: {1}/{2}. Server response: {3}.'.format(
                        resource_handler.name, r.status_code,
                        http.client.responses.get(r.status_code,"(undefined http code returned by CloudSigma API)"), r.text)
          log.debug(error_msg)
          log.debug('Returned json: {0}'.format(r.json().dumps()))
          log.debug('Retrying in {0} seconds...  {1} attempts left.'.format(wait_time_between_api_call_retries,retry))
          time.sleep(wait_time_between_api_call_retries)
          retry-=1
          r = requests.post(resource_handler.endpoint + '/servers/',
              auth=get_auth(resource_handler.auth_data), json=json_data)
        if r.status_code != 201:
            error_msg = '[{0}] Failed to create server! HTTP response code/message: {1}/{2}. Server response: {3}.'.format(
                        resource_handler.name, r.status_code,
                        http.client.responses.get(r.status_code,"(undefined http code returned by CloudSigma API)"), r.text)
            return None, error_msg
        srv_uuid = r.json()['objects'][0]['uuid']
        log.debug('[%s] Created server\'s UUID is: %s', resource_handler.name, srv_uuid)
        return srv_uuid, ""

    @wet_method()
    def _delete_server(self, resource_handler, srv_id):
        r = requests.delete(resource_handler.endpoint + '/servers/' + srv_id + '/',
            auth=get_auth(resource_handler.auth_data), params={'recurse': 'all_drives'},
            headers={'Content-type': 'application/json'})
        retry=max_number_of_api_call_retries
        while r.status_code != 204 and retry>0:
          error_msg = '[{0}] Failed to delete server {1}! HTTP response code/message: {2}/{3}. Server response: {4}.'.format(
            resource_handler.name, srv_id, r.status_code,
            http.client.responses.get(r.status_code,"(undefined http code returned by CloudSigma API)"), r.text)
          log.debug(error_msg)
          log.debug('Retrying in {0} seconds...  {1} attempts left.'.format(wait_time_between_api_call_retries,retry))
          time.sleep(wait_time_between_api_call_retries)
          retry-=1
          r = requests.delete(resource_handler.endpoint + '/servers/' + srv_id + '/',
              auth=get_auth(resource_handler.auth_data), params={'recurse': 'all_drives'},
              headers={'Content-type': 'application/json'})
        if r.status_code != 204:
            error_msg = '[{0}] Failed to delete server {1}! HTTP response code/message: {2}/{3}. Server response: {4}.'.format(
                        resource_handler.name, srv_id, r.status_code,
                        http.client.responses.get(r.status_code,"(undefined http code returned by CloudSigma API)"), r.text)
            return error_msg
        return None

    @wet_method([True,""])
    def _start_server(self, resource_handler, srv_id):
        r = requests.post(resource_handler.endpoint + '/servers/' + srv_id + '/action/',
            auth=get_auth(resource_handler.auth_data), params={'do': 'start'})
        if r.status_code != 202:
            error_msg = '[{0}] Failed to start server {1}! HTTP response code/message: {2}/{3}. Server response: {4}.'.format(
                        resource_handler.name, srv_id, r.status_code,
                        http.client.responses.get(r.status_code,"(undefined http code returned by CloudSigma API)"), r.text)
            return False, error_msg
        return True, ""

    @wet_method([True,""])
    def _stop_server(self, resource_handler, srv_id):
        r = requests.post(resource_handler.endpoint + '/servers/' + srv_id + '/action/',
            auth=get_auth(resource_handler.auth_data), params={'do': 'stop'})
        if r.status_code != 202:
             error_msg = '[{0}] Failed to stop server {1}! HTTP response code/message: {2}/{3}. Server response: {4}.'.format(
                         resource_handler.name, srv_id, r.status_code,
                         http.client.responses.get(r.status_code,"(undefined http code returned by CloudSigma API)"), r.text)
             return False, error_msg
        return True, ""

    def perform(self, resource_handler):
        log.debug("[%s] Creating node: %r",
                  resource_handler.name, self.resolved_node_definition['name'])
        drv_id, srv_id = None, None
        try:
            drv_id, errormsg = self._clone_drive(resource_handler, self.resolved_node_definition['resource']['libdrive_id'])
            if not drv_id:
                log.error(errormsg)
                raise NodeCreationError(None, errormsg)
            drv_st, errormsg = self._get_drive_status(resource_handler, drv_id)
            while drv_st != 'unmounted':
                log.debug("[%s] Waiting for cloned drive to enter unmounted state, currently %r",resource_handler.name, drv_st)
                time.sleep(wait_time_between_api_call_retries)
                drv_st, errormsg = self._get_drive_status(resource_handler, drv_id)
            srv_id, errormsg = self._create_server(resource_handler, drv_id)
            if not srv_id:
                log.error(errormsg)
                self._delete_drive(resource_handler, drv_id)
                raise NodeCreationError(None, errormsg)
            srv_st = get_server_status(resource_handler, srv_id)
            while srv_st not in ['starting','started','running']:
                log.debug("[%s] Server is in %s state. Waiting to enter starting state...",
                           resource_handler.name, srv_st)
                if srv_st == 'stopped':
                  ret, errormsg = self._start_server(resource_handler, srv_id)
                  if not ret:
                     log.debug(errormsg)
                time.sleep(wait_time_between_api_call_retries)
                srv_st = get_server_status(resource_handler, srv_id)
        except KeyboardInterrupt:
            log.info('Interrupting node creation! Rolling back. Please, stand by!')
            if srv_id:
                srv_st = get_server_status(resource_handler, srv_id)
                while srv_st not in ['stopped','unknown']:
                    log.debug("[%s] Server is in %s state.",resource_handler.name, srv_st)
                    time.sleep(wait_time_between_api_call_retries)
                    if srv_st != 'stopping':
                      self._stop_server(resource_handler, srv_id)
                    srv_st = get_server_status(resource_handler, srv_id)
                self._delete_server(resource_handler, srv_id)
            # if drv_id:
            #     drv_st, _ = self._get_drive_status(resource_handler, drv_id)
            #     while drv_st not in ['unmounted','unknown']:
            #         log.debug("[%s] Drive is in %s state.",resource_handler.name, drv_st)
            #         time.sleep(wait_time_between_api_call_retries)
            #         drv_st, _ = self._get_drive_status(resource_handler, drv_id)
            #     self._delete_drive(resource_handler, drv_id)
            raise
        return srv_id

class DropNode(Command):
    def __init__(self, instance_data):
        Command.__init__(self)
        self.instance_data = instance_data

    @wet_method([True,""])
    def _stop_server(self, resource_handler, srv_id):
        r = requests.post(resource_handler.endpoint + '/servers/' + srv_id + '/action/',
            auth=get_auth(resource_handler.auth_data), params={'do': 'stop'})
        if r.status_code != 202:
             error_msg = '[{0}] Failed to stop server {1}! HTTP response code/message: {2}/{3}. Server response: {4}.'.format(
                         resource_handler.name, srv_id, r.status_code,
                         http.client.responses.get(r.status_code,"(undefined http code returned by CloudSigma API)"), r.text)
             return False, error_msg
        return True, ""

    @wet_method()
    def _delete_server(self, resource_handler, srv_id):
        r = requests.delete(resource_handler.endpoint + '/servers/' + srv_id + '/',
            auth=get_auth(resource_handler.auth_data), params={'recurse': 'all_drives'},
            headers={'Content-type': 'application/json'})
        retry=max_number_of_api_call_retries
        while r.status_code != 204 and retry>0:
          error_msg = '[{0}] Failed to delete server {1}! HTTP response code/message: {2}/{3}. Server response: {4}.'.format(
            resource_handler.name, srv_id, r.status_code,
            http.client.responses.get(r.status_code,"(undefined http code returned by CloudSigma API)"), r.text)
          log.debug(error_msg)
          log.debug('Retrying in {0} seconds...  {1} attempts left.'.format(wait_time_between_api_call_retries,retry))
          time.sleep(wait_time_between_api_call_retries)
          retry-=1
          r = requests.delete(resource_handler.endpoint + '/servers/' + srv_id + '/',
              auth=get_auth(resource_handler.auth_data), params={'recurse': 'all_drives'},
              headers={'Content-type': 'application/json'})
        if r.status_code != 204:
            error_msg = '[{0}] Failed to delete server {1}! HTTP response code/message: {2}/{3}. Server response: {4}.'.format(
                        resource_handler.name, srv_id, r.status_code,
                        http.client.responses.get(r.status_code,"(undefined http code returned by CloudSigma API)"), r.text)
            return error_msg
        return None

    @wet_method()
    def perform(self, resource_handler):
        """
        Terminate a VM instance.

        :param instance_data: Information necessary to access the VM instance.
        :type instance_data: :ref:`Instance Data <instancedata>`
        """
        srv_id = self.instance_data.get('instance_id')
        if not srv_id:
            return

        log.debug("[%s] Deleting server %r", resource_handler.name,
                self.instance_data['node_id'])

        srv_st = get_server_status(resource_handler, srv_id)
        while srv_st not in ['stopped','unknown']:
            log.debug("[%s] Server is in %s state. Waiting for \"stopped\" state...",resource_handler.name, srv_st)
            if srv_st != 'stopping':
              self._stop_server(resource_handler, srv_id)
            time.sleep(wait_time_between_api_call_retries)
            srv_st = get_server_status(resource_handler, srv_id)
        self._delete_server(resource_handler, srv_id)

        log.debug("[%s] Deleting server: done", resource_handler.name)

class GetState(Command):
    def __init__(self, instance_data):
        Command.__init__(self)
        self.instance_data = instance_data

    @wet_method(status.READY)
    def perform(self, resource_handler):
        srv_id = self.instance_data['instance_id']
        srv_st = get_server_status(resource_handler, srv_id)
        try:
            retval = STATE_MAPPING[srv_st]
        except KeyError:
            raise NotImplementedError('Unknown CloudSigma server state', srv_st)
        else:
            log.debug("[%s] Done; cloudsigma_state=%r; status=%r",
                      resource_handler.name, srv_st, retval)
            return retval

class GetIpAddress(Command):
    def __init__(self, instance_data):
        Command.__init__(self)
        self.instance_data = instance_data

    @wet_method('127.0.0.1')
    def perform(self, resource_handler):
        srv_id = self.instance_data['instance_id']
        rv = ''
        json_data = get_server_json(resource_handler, srv_id)
        if json_data == None:
            return rv
        if json_data.get('runtime',None) == None:
            return rv
        if 'nics' not in json_data['runtime']:
            return rv
        for nic in json_data['runtime']['nics']:
            if nic == None:
              continue
            if nic.get('ip_v4') is not None:
              return nic.get('ip_v4').get('uuid',rv)
        return rv

class GetAddress(Command):
    def __init__(self, instance_data):
        Command.__init__(self)
        self.instance_data = instance_data

    @wet_method('127.0.0.1')
    def perform(self, resource_handler):
        srv_id = self.instance_data['instance_id']
        rv = ''
        json_data = get_server_json(resource_handler, srv_id)
        if json_data == None:
            return rv
        if json_data.get('runtime',None) == None:
            return rv
        if 'nics' not in json_data['runtime']:
            return rv
        for nic in json_data['runtime']['nics']:
            if nic == None:
              continue
            if nic.get('ip_v4') is not None:
              return nic.get('ip_v4').get('uuid',rv)
        return rv

@factory.register(ResourceHandler, PROTOCOL_ID)
class CloudSigmaResourceHandler(ResourceHandler):
    """ Implementation of the
    :class:`~occo.resourcehandler.resourcehandler.ResourceHandler` class utilizing the
    CloudSigma_ RESTful_ interface.

    :param str endpoint: Endpoint of the CloudSigma service URL.
    :param dict auth_data: Authentication infomration for the connection.

        * ``email``: The e-mail address used to log in.
        * ``password``: The password belonging to the e-mail address.

    :param str name: The name of this ``ResourceHandler`` instance. If unset,
        ``endpoint`` is used.
    :param bool dry_run: Skip actual resource aquisition, polling, etc.

    .. _CloudSigma: https://www.cloudsigma.com/
    .. _RESTful: https://en.wikipedia.org/wiki/Representational_state_transfer
    """
    def __init__(self, endpoint, auth_data,
                 name=None, dry_run=False,
                 **config):
        self.dry_run = dry_run
        self.name = name if name else endpoint
        if (not auth_data) or (not "email" in auth_data) or (not "password" in auth_data):
           errormsg = "Cannot find credentials for \""+endpoint+"\". Please, specify!"
           log.debug(errormsg)
           raise NodeCreationError(None, errormsg)
        self.endpoint = endpoint if not dry_run else None
        self.auth_data = auth_data if not dry_run else None

    def cri_create_node(self, resolved_node_definition):
        return CreateNode(resolved_node_definition)

    def cri_drop_node(self, instance_data):
        return DropNode(instance_data)

    def cri_get_state(self, instance_data):
        return GetState(instance_data)

    def cri_get_address(self, instance_data):
        return GetAddress(instance_data)

    def cri_get_ip_address(self, instance_data):
        return GetIpAddress(instance_data)

    def perform(self, instruction):
        instruction.perform(self)

@factory.register(RHSchemaChecker, PROTOCOL_ID)
class CloudSigmaSchemaChecker(RHSchemaChecker):
    def __init__(self):
        self.req_keys = ["type", "endpoint", "libdrive_id", "description"]
        self.req_desc_keys = ['cpu', 'mem', 'vnc_password']
        self.opt_keys = ["name"]
    def perform_check(self, data):
        missing_keys = RHSchemaChecker.get_missing_keys(self, data, self.req_keys)
        if missing_keys:
            msg = "Missing key(s): " + ', '.join(str(key) for key in missing_keys)
            raise SchemaError(msg)
        missing_keys = RHSchemaChecker.get_missing_keys(self, data['description'], self.req_desc_keys)
        if missing_keys:
            msg = "Missing key(s) in description: " + ', '.join(str(key) for key in missing_keys)
            raise SchemaError(msg)
        valid_keys = self.req_keys + self.opt_keys
        invalid_keys = RHSchemaChecker.get_invalid_keys(self, data, valid_keys)
        if invalid_keys:
            msg = "Unknown key(s): " + ', '.join(str(key) for key in invalid_keys)
            raise SchemaError(msg)
        return True
