from collections import defaultdict, deque
import select
import sys
import time
import hashlib
import datetime

try:
    unicode
except NameError:
    unicode = str
    basestring = str

from metaflow.exception import MetaflowException
from metaflow.metaflow_config import get_kubernetes_client,KUBE_NAMESPACE

import kubernetes.client as kube_client
from kubernetes import watch
from kubernetes.client.rest import ApiException

MAX_MEMORY = 32*1000
MAX_CPU = 8

    
class KubeClient(object):
    def __init__(self):
        # todo : set the 
        self._client = get_kubernetes_client()

    def unfinished_jobs(self):
        """unfinished_jobs [Gets the Kube jobs which are unfinished.]
        
        :return: [tuple with KubeJobSpec Objects]
        :rtype: [Tuple(KubeJobSpec)]
        """
        # $ (TODO) : NAMESPACE NEEDS TO COME FROM ENV VAR. 
        # ! NAMESPACES NEED TO BE FIXED. NEED TO DECLARE AN ENV VAR FOR MY Kubernetes EVN. 
        # $ Get the Jobs.
        jobs = kube_client.BatchV1Api(self._client).list_namespaced_job(KUBE_NAMESPACE,include_uninitialized=False,timeout_seconds=60)
        return (KubeJobSpec(self._client,job.metadata.name,job.metadata.namespace).update() for job in jobs.items if job.status.active is not None)

    def job(self):
        return KubeJob(self._client)

    def attach_job(self, job_name,namespace):
        job = RunningKubeJob(self._client,job_name,namespace)
        return job.update()


class KubeJobException(MetaflowException):
    headline = 'Kube job error'


class KubeJobSpecException(MetaflowException):
    headline = 'Kube job Exception'

class KubeJob(object):
    """KubeJob [summary]
    
    :param object: [description]
    :type object: [type]
    :raises KubeJobException: [description]
    :return: [description]
    :rtype: [type]
    """
    def __init__(self, client):
        self._client = client
        self._api_client = kube_client.BatchV1Api(client)
        self.payload = kube_client.V1Job(api_version="batch/v1", kind="Job")
        self.payload.metadata = kube_client.V1ObjectMeta()
        self.payload.metadata.labels = dict()
        self.payload.status = kube_client.V1JobStatus()
        self.namespace_name = None
        self.name = None
        # self.template = kube_client.V1PodTemplate()
        self.template = kube_client.V1PodTemplateSpec()
        self.env_list = []
        self.params = []
        self._image = None
        self.container = kube_client.V1Container(name='metaflow-job') 
        self.command_value = None # $ Need to figure how to structure this properly. 
        self.container.resources = kube_client.V1ResourceRequirements(limits={'cpu':str(MAX_CPU*1000)+"m",'memory':str(MAX_MEMORY)+"Mi"},requests={}) # $ NOTE: Currently Setting Hard Limits. Will Change Later


    def execute(self):
        """execute [Runs the Job and yields a RunningKubeJob object]
        :raises KubeJobException: [Upon failure of submitting Job for Execution]
        :return: RunningKubeJob or None
        :rtype: [RunningKubeJob]
        """
        if self._image is None:
            raise KubeJobException(
                'Unable to launch Kubernetes Job job. No docker image specified.'
            )
        if self.namespace_name is None:
            raise KubeJobException("Unable to launch Kubernetes Job Without Namespace.")

        self.container.image = self._image
        self.container.command = [self.command_value[0]]
        self.container.args = self.command_value[1:]
        self.container.env = self.env_list

        self.template.spec = kube_client.V1PodSpec(containers=[self.container],restart_policy='Never')
        self.payload.spec = kube_client.V1JobSpec(ttl_seconds_after_finished=600, template=self.template)
        try: 
            api_response = self._api_client.create_namespaced_job(self.namespace_name,body=self.payload)
            # $ Returning from within try to ensure There was correct Response
            job = RunningKubeJob(self._client,self.name,self.namespace_name)
            return job.update()
        except ApiException as e:
            # $ (TODO) : TEST AND CHECK IF THE EXCEPTION BEING RAISED IS APPROPRIATEDLY CAUGHT
            print(e)
            raise KubeJobException("Exception when calling API: %s\n" % e)
            return None


    def parameter(self,key, value):
        self.params.append({key:value})
        return self

    # $ (TODO) : Need to handle really long Job Names
    def job_name(self, job_name):
        self.payload.metadata.name = job_name
        self.name = job_name 

        return self

    # $ (TODO) : TEST THIS FUNCTION HERER.. 
    def meta_data_label(self,key,value):
        self.payload.metadata.labels[key] = value
        return self

    def namespace(self,namespace_name):
        self.namespace_name = namespace_name
        return self

    def image(self, image):
        self._image = image
        return self

    def args(self,args):
        if not isinstance(args,list) :
            raise KubeJobException("Invalid Args Type. Needs to be Of Type List but got {}".format(type(args)))
        self.container.args = args
        return self

    def command(self, command):
        if not isinstance(command,list) :
            raise KubeJobException("Invalid Command Type. Needs to be Of Type List but got {}".format(type(command)))
        # self.container.command = command
        self.command_value = command
        return self

    def cpu(self, cpu):
        if not (isinstance(cpu, (int, unicode, basestring)) and int(cpu) > 0):
            raise KubeJobException(
                'Invalid CPU value ({}); it should be greater than 0'.format(cpu))
        self.container.resources.requests['cpu'] = str(int(cpu)*1000)+"m"
        return self

    def memory(self, mem):
        if not (isinstance(mem, (int, unicode, basestring)) and int(mem) > 0):
            raise KubeJobException(
                'Invalid memory value ({}); it should be greater than 0'.format(mem))
        self.container.resources.requests['memory'] = str(mem)+"Mi"
        return self

    # $ (TODO) : CONFIGURE GPU RELATED STUFF HERE
    def gpu(self, gpu):
        if not (isinstance(gpu, (int, unicode, basestring))):
            raise KubeJobException(
                'invalid gpu value: ({}) (should be 0 or greater)'.format(gpu))
        if int(gpu) > 0:
            pass # $ todo : Figure GPU Here. 
        return self

    def environment_variable(self, name, value):
        self.env_list.append(kube_client.V1EnvVar(name=name,value=value))
        return self

    # $ (TODO) : CHECK JOB CONFIGS TO SEE HOW LONG TO PERSIST A JOB AFTER COMPLETION/FAILURE
    def timeout_in_secs(self, timeout_in_secs):
        # self.
        # self.payload['timeout']['attemptDurationSeconds'] = timeout_in_secs
        return self


class limit(object):
    def __init__(self, delta_in_secs):
        self.delta_in_secs = delta_in_secs
        self._now = None

    def __call__(self, func):
        def wrapped(*args, **kwargs):
            now = time.time()
            if self._now is None or (now - self._now > self.delta_in_secs):
                func(*args, **kwargs)
                self._now = now
        return wrapped


class KubeJobSpec(object):
    """KubeJobSpec 
    The purpose if this class is to bind with Job Related highlevel object will API's of Kubernetes.
    This binds the object to job name and namespace. running the KubeJobSpec().update() will update the object 
    with the latest observations from Kubernetes. The updates are stored in the _data property. We use properties of a 
    job such as 'id','job_name','status','created_at', 'is_done' etc as high level abstractions to what the Object that kubernetes
    api returns. We achieve this using @property decorator.
    
    Parameter Behaviour : 

    'update_once' : 
        If True:
            Will ensure that the bound Object will only update once. During updation, if there is failure the job will raise 
            an exception. 

        If False:
            It will update When ever some property requires it to be called. such as 'is_done', 'is_running',
    
    :raises KubeJobSpecException: If bound API Fails it will raise Exception. 
    """
    def __init__(self,client,job_name,namespace,update_once=True):
        super().__init__()
        self._client = client
        self._batch_api_client = kube_client.BatchV1Api(client)
        self.name = job_name
        self.updated = False
        self.update_once = update_once
        self.namespace = namespace

    def __repr__(self):
        return '{}(\'{}\')'.format(self.__class__.__name__, self._id)

    def _apply(self, data):
        self.updated = True
        self._data = data

    @limit(1)
    def _update(self):
        try:
            # $ https://github.com/kubernetes-client/python/blob/master/kubernetes/docs/BatchV1Api.md#read_namespaced_job
            data = self._batch_api_client.read_namespaced_job(self.name,self.namespace)
        except ApiException as e :
            if self.update_once:
                # $ (TODO) : See if this is a good Way of Managing Exceptions. Should update_once be allowed to keep api Exceptions
                raise KubeJobSpecException('Error in read_namespaced_job API %s'%str(e))
            return 
        self._apply(data)

    def update(self):
        if not self.updated or not self.update_once:
            self._update()
        return self
    
    @property
    # $ (TODO) : TEST THIS FUNCTION TO CHECK IF the if CONDITION IS EVER NEEDED OR NOT
    def id(self): # ! NEED TO CHECK IF THIS SHOULD BE DONE OR NOT.
        return self.info.metadata.uid

    @property
    def info(self):
        return self._data

    @property
    def job_name(self):
    # $ (TODO) : TEST THIS FUNCTION TO CHECK IF the if CONDITION IS EVER NEEDED OR NOT
        return self.info.metadata.name
    
    @property
    def labels(self):
        return self.info.metadata.labels

    @property
    def status(self):
        if self.is_running:
            return 'RUNNING'
        elif self.is_successful:
            return 'COMPLETED'
        elif self.is_crashed:
            return 'FAILED'
        else:
            return 'UNKNOWN_STATUS'
    
    # $ TODO : Add Metadata Labels As a Property. 

    @property
    def status_reason(self):
        return self.reason

    @property
    def created_at(self):
    # $ (TODO) : TEST THIS FUNCTION TO CHECK IF the if CONDITION IS EVER NEEDED OR NOT
        return self.info.status.start_time

    @property
    def is_done(self):
    # $ (TODO) : TEST THIS FUNCTION TO CHECK IF the if CONDITION IS EVER NEEDED OR NOT
        if self.info.status.completion_time is None:
            self.update()
        return self.info.status.completion_time is not None

    @property
    def is_running(self):
    # $ (TODO) : TEST THIS FUNCTION TO CHECK IF the if CONDITION IS EVER NEEDED OR NOT
        if self.info.status.active == 1:
            self.update()
        return self.info.status.active == 1

    @property
    def is_successful(self):
    # $ (TODO) : TEST THIS FUNCTION TO CHECK IF the if CONDITION IS EVER NEEDED OR NOT
        return self.info.status.succeeded is not None

    @property
    def is_crashed(self):
    # $ (TODO) : TEST THIS FUNCTION TO CHECK IF the if CONDITION IS EVER NEEDED OR NOT
        # TODO: Check statusmessage to find if the job crashed instead of failing
        return self.info.status.failed is not None

    @property
    def reason(self):
    # $ (TODO) : TEST THIS FUNCTION TO CHECK IF the if CONDITION IS EVER NEEDED OR NOT
        reason = []
        if self.info.status.conditions is not None:
            for obj in self.info.status.conditions:
                if obj.reason is not None:
                    reason.append(obj.reason)

        return '\n'.join(reason)


# $ ? Doubt : Why do u inherit Object ?
class RunningKubeJob(KubeJobSpec):

    NUM_RETRIES = 5

    def __init__(self, client, job_name, namespace, update_once=False):
        super().__init__(client, job_name, namespace, update_once=update_once)

    # $ https://stackoverflow.com/questions/56124320/how-to-get-log-and-describe-of-pods-in-kubernetes-by-python-client
    def logs(self):
        pod_label_selector = "controller-uid=" + self.info.spec.template.metadata.labels.get('controller-uid')
        pods_list = kube_client.CoreV1Api(self._client).list_namespaced_pod(self.namespace,label_selector=pod_label_selector, timeout_seconds=10)
        pod_name = pods_list.items[0].metadata.name
        # There is no Need to check if the job is in runnable state as the Job will be runnnig on Kube
        watcher = watch.Watch()
        for i in range(self.NUM_RETRIES):
            if self.is_done:
                break
            try:
                check_after_done = 0
                # last_call = time()
                for line in watcher.stream(kube_client.CoreV1Api(self._client).read_namespaced_pod_log, name=pod_name, namespace=self.namespace):
                    # start_time = datetime.datetime.now()
                    if not line:
                        if self.is_done:
                            if check_after_done > 1:
                                return
                            check_after_done += 1
                        else:
                            pass
                    else:
                        yield line
                break # Because this is a generator, we want to break out here because this means that we are done printing all logs. 
            except Exception as ex:
                if self.is_crashed:
                    break
                # sys.stderr.write('Except : '+str(i))
                time.sleep(2 ** i)
            

    def kill(self):
        # $ (TODO) : TEST THIS FUNCTION TO CHECK IF THE DELETE IS HAPPENING PROPERLY 
        if not self.is_done:
            self._batch_api_client.delete_namespaced_job(self.name,self.namespace)
        return self.update()