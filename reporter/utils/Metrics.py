from prometheus_client import CollectorRegistry
from prometheus_client import Gauge, Counter, Summary, Histogram
from prometheus_client.core import GaugeMetricFamily
from prometheus_client import write_to_textfile, push_to_gateway
from prometheus_client.exposition import basic_auth_handler
from prometheus_client.parser import text_string_to_metric_families

import potsdb
import datetime
import logging

from utils.Cluster import *
from utils.Common  import *

class MetricData:
    """data object for metric data"""
    
    def __init__(self, tags, value):
        self.value = value
        self.tags  = tags
        
    def __str__(self):
        return pprint.pformat(self.__dict__)

    def __repr__(self):
        return repr(self.__dict__)

    def __eq__(self, other):
        if not isinstance(other, MetricData):
            raise NotImplementedError
        return self.tags == other.tags

class ClusterAccounting:
    """metrics collector for cluster utilisation accounting"""
    def __init__(self, config, lv=logging.ERROR):
        
        self.logger = getMyLogger(self.__class__.__name__)
        self.logger.setLevel(lv)
    
        ## load config file and global settings
        c = getConfig(config)
        self.TORQUE_LOG_DIR      = c.get('TorqueTracker','TORQUE_LOG_DIR')
        self.BIN_QSTAT_ALL       = c.get('TorqueTracker','BIN_QSTAT_ALL')
        self.BIN_FSHARE_ALL      = c.get('TorqueTracker','BIN_FSHARE_ALL')
        self.TORQUE_BATCH_QUEUES = c.get('TorqueTracker','TORQUE_BATCH_QUEUES').split(',')
        
        self.OPENTSDB_HOST = c.get('MetricsPusher','OPENTSDB_HOST')
        self.OPENTSDB_PORT = int(c.get('MetricsPusher','OPENTSDB_PORT'))
        
        ## The registry contains metrics data in the following format
        ##
        ## key: metric name
        ## value: [ MetricData_1, MetricData_2, ... ]
        self.registry = {'hpc_acct_wtime_asked': [],
                         'hpc_acct_wtime_used' : [],
                         'hpc_acct_mem_asked'  : [],
                         'hpc_acct_mem_used'   : [],
                         'hpc_acct_ctime_used' : [],
                         'hpc_acct_job_count'  : []}
    
    def exportToFile(self, fpath):
        """export metrics in the registry to a file"""
        f = open(fpath, 'w')
        for m,d in self.registry.iteritems():
            for x in d:
                f.write("%s %s %s\n" % (m, repr(x.tags), x.value))
        f.close()
        return

    def pushMetrics(self, host=None, **kwargs):
        
        if not host:
            host = self.OPENTSDB_HOST
        
        port=self.OPENTSDB_PORT
        qsize=100000
        host_tag=True
        mps=0
        check_host=True

        for k in kwargs.keys():
            if k == 'port':
                port = int(kwargs[k])
                continue
            if k == 'qsize':
                qsize = int(kwargs[k])
                continue
            if k == 'host_tag':
                host_tab = bool(kwargs[k])
                continue
            if k == 'mps':
                mps = int(kwargs[k])
                continue
            if k == 'check_host':
                check_host = bool(kwargs[k])
                continue

        metrics = potsdb.Client(host, port=port, qsize=qsize, host_tag=host_tag, mps=mps, check_host=check_host)
        
        # send data to openTSDB
        for m,d in self.registry.iteritems():
            for x in d:
                metrics.send(m, x.value, **x.tags)

        # wait until data are being sent out
        metrics.wait()

    def collectMetrics(self, date=None):
        """collection metrics"""

        if not date:
            date = (datetime.date.today() - datetime.timedelta(1)).strftime('%Y%m%d')

        self.logger.info('collecting data of jobs submitted on %s' % date)
        jobs = get_complete_jobs(self.TORQUE_LOG_DIR, date, debug=(self.logger.level == logging.DEBUG) )
        
        for j in filter(lambda x:(x.cwtime and x.cmem and x.cctime), jobs):

            # TODO: make the time resolution configurable, currently it's hardcoded as 1 hour = 3600 seconds
            ts = long(j.t_finish) - ( long(j.t_finish) % 3600 ) + 1800

            # TODO: convert gid to meaninful value?
            s = interpret_job_ec(j.jec)
            t  = {'gid':str(j.gid), 'uid':str(j.uid), 'jstat':s, 'jqueue':j.queue, 'timestamp': ts}
            
            # construct data points for this job
            data = {'hpc_acct_wtime_asked': MetricData(tags=t, value=j.rwtime),
                    'hpc_acct_mem_asked'  : MetricData(tags=t, value=j.rmem),
                    'hpc_acct_wtime_used' : MetricData(tags=t, value=j.cwtime),
                    'hpc_acct_mem_used'   : MetricData(tags=t, value=j.cmem),
                    'hpc_acct_ctime_used' : MetricData(tags=t, value=j.cctime),
                    'hpc_acct_job_count'  : MetricData(tags=t, value=1)}
            
            # update registry
            for m,d in data.iteritems():
                try:
                    idx = self.registry[m].index(d)
                    self.registry[m][idx].value += d.value
                except ValueError:
                    self.registry[m].append(d)
                except:
                    pass
                    
class MatlabLicenseAccounting(ClusterAccounting):
    """metrics collector for matlab license usage"""
    def __init__(self, config, lv=logging.ERROR):
        
        ClusterAccounting.__init__(self,config,lv)
        
        ## load config file and global settings
        c = getConfig(config)
        self.BIN_CLUSTER_MATLAB = c.get('TorqueTracker','BIN_CLUSTER_MATLAB')       
        self.registry = {'hpc_acct_matlab_license_usage':[]}
        
    def collectMetrics(self, date=None):
        """collection metrics"""
        now = time.time()
        self.logger.debug('getting matlab license usage ...')
        licenses = get_matlab_license_usage(self.BIN_CLUSTER_MATLAB)
        m = 'hpc_acct_matlab_license_usage'
        for l in licenses:
            d = MetricData(tags={'package': l.package, 'host':l.host, 'timestamp': now}, value=1)
            try:
                idx = self.registry[m].index(d)
                self.registry[m][idx].value += d.value
            except ValueError:
                self.registry[m].append(d)
            except:
                pass

class ClusterStatistics:
    """metrics collector for cluster statistics"""
    def __init__(self, config, lv=logging.ERROR):
        
        self.logger = getMyLogger(self.__class__.__name__)
        self.logger.setLevel(lv)
        
        ## load config file and global settings
        c = getConfig(config)
        self.BIN_QSTAT_ALL       = c.get('TorqueTracker','BIN_QSTAT_ALL')
        self.BIN_FSHARE_ALL      = c.get('TorqueTracker','BIN_FSHARE_ALL')
        self.TORQUE_BATCH_QUEUES = c.get('TorqueTracker','TORQUE_BATCH_QUEUES').split(',')
        
        self.PROMETHEUS_GW_HOST = c.get('MetricsPusher', 'PROMETHEUS_GW_HOST')
        self.PROMETHEUS_GW_PORT = c.get('MetricsPusher', 'PROMETHEUS_GW_PORT')
        
        self.registry = CollectorRegistry()

        # maintain a list of nodes that are in status 'down'
        self.nodes_down = []
    
    def exportToFile(self, fpath):
        """export metrics in the registry to a file"""
        write_to_textfile(fpath, self.registry)
        return
        
    def getMetrics(self, name, **labels):
        """get latest value of a given metrics with name and labels"""
        
        t = CURLCallback()
        c = pycurl.Curl()
        c.setopt(c.URL, "http://%s:%s/metrics" % (self.PROMETHEUS_GW_HOST, self.PROMETHEUS_GW_PORT))
        c.setopt(c.CONNECTTIMEOUT, 3)
        c.setopt(c.TIMEOUT, 10)
        c.setopt(c.HEADERFUNCTION, t.header_callback)
        c.setopt(c.WRITEFUNCTION , t.body_callback)
        c.setopt(c.CUSTOMREQUEST , 'GET')
        c.perform()
        code = c.getinfo(pycurl.HTTP_CODE)
        c.close()

        self.logger.debug('dump metrics from %s (HTTP CODE: %d)' % (self.PROMETHEUS_GW_HOST, code))

        m = []
        for family in text_string_to_metric_families(t.contents):
            for sample in family.samples:
                if sample[0] == name and all(item in sample[1].items() for item in labels.items()):
                    m.append(sample)
        return m

    def pushMetrics(self, endpoint=None, **kwargs):
        """push metrics in the registry to the prometheus gateway"""

        if not endpoint:
            endpoint = '%s:%s' % (self.PROMETHEUS_GW_HOST, self.PROMETHEUS_GW_PORT)

        job = 'hpc_metrics'
        try:
            job = kwargs['job']
        except:
            pass

        push_to_gateway(endpoint, job=job, registry=self.registry)
        return
    
    def collectMetrics(self):
        
        # metrics for core utilisation
        g_core_usage = Gauge('hpc_stat_core_usage', 'number of used cores per node per queue', ['host', 'queue'], registry=self.registry)
        
        # metrics for memory utilisation
        g_mem_usage  = Gauge('hpc_stat_mem_usage' , 'bytes of used memory per node per queue', ['host', 'queue'], registry=self.registry)
        
        # metrics for node specification
        g_node_status = Gauge('hpc_stat_node_status', 'node status (-1:down, 0:offline, 1:job-exclusive, 2:free, 3: other)', ['host'], registry=self.registry)
        g_core_total = Gauge('hpc_stat_core_total', 'number of total cores per node', ['host'], registry=self.registry)
        g_mem_total  = Gauge('hpc_stat_mem_total' , 'bytes of total memory per node', ['host'], registry=self.registry)
        g_network_total = Gauge('hpc_stat_network_total' , 'Gbits of network bandwidth', ['host'], registry=self.registry)
        g_gpu_total  = Gauge('hpc_stat_gpu_total' , 'number of total gpus per node', ['host'], registry=self.registry)
        
        # metrics for job count per queue, per state
        g_job_count  = Gauge('hpc_stat_job_count' , 'number of jobs' , ['queue','status','host'], registry=self.registry)

        jobs = get_qstat_jobs(s_cmd=self.BIN_QSTAT_ALL)
        nodes = get_cluster_node_properties()

        # TODO: make it more transparent
        n_status = { 'down'          : -1,
                     'offline'       :  0,
                     'job-exclusive' :  1,
                     'free'          :  2,
                     'other'         :  3 }

        # TODO: make it configurable
        q_cat = ['matlab','batch','vgl','interact','other']

        def _qcat(queue):
            cat = queue

            if cat in self.TORQUE_BATCH_QUEUES:
                cat = 'batch'
            elif queue not in q_cat:
                cat = 'other'

            return cat
 
        # static node information
        self.nodes_down = []
        for n in nodes:
            g_core_total.labels(host=n.host).set( n.ncores )
            g_mem_total.labels(host=n.host).set( n.mem * 1000000000 )
            g_network_total.labels(host=n.host).set( int(n.net.replace('network','').replace('GigE','')) )
            g_gpu_total.labels(host=n.host).set( n.ngpus )

            if n.stat in n_status.keys(): 
                g_node_status.labels(host=n.host).set( n_status[n.stat] )
                if n_status[n.stat] == n_status['down']:
                    self.nodes_down.append(n.host)
            else:
                g_node_status.labels(host=n.host).set( n_status['other'] )

            # add extra attribute to match the interactive queue names defined in q_cat
            n.__dict__['interact'] = n.__dict__['interactive']
            n.__dict__['other']    = True

            # set default usage metrics to zero
            for q in q_cat:
                g_core_usage.labels(queue=q, host=n.host).set( 0 )
                g_mem_usage.labels(queue=q , host=n.host).set( 0 )

        #   the job state (according to qstat manual):
        #     C  - Job is completed after having run
        #     E  - Job is exiting after having run.
        #     H  - Job is held.
        #     Q  - Job is queued, eligible to run or routed.
        #     R  - Job is running.
        #     T  - Job is being moved to new location.
        #     W  - Job is waiting for its execution time (-a option) to be reached.
        #     S  - (Unicos only) job is suspended.        
        
        ## get jobs in queue or waiting state
        _jobs = []
        map(_jobs.extend, jobs.values())

        q_jobs = filter(lambda j:j.jstat in ['Q','S'], _jobs)
        h_jobs = filter(lambda j:j.jstat in ['H'], _jobs)

        # set default job count metrics to zero
        for q in q_cat:
            for s in ['queued','held','running']:
                if s == 'running':
                    # loop over hosts to set initial value of zero for accepted queues
                    for n in filter(lambda x:x.__dict__[q], nodes):
                        g_job_count.labels(queue=q, status=s, host=n.host).set(0)
                else:
                    g_job_count.labels(queue=q, status=s, host='na').set(0)

        for j in q_jobs:
            g_job_count.labels(queue=_qcat(j.queue), status='queued', host='na').inc(1)
            
        for j in h_jobs:
            g_job_count.labels(queue=_qcat(j.queue), status='held', host='na').inc(1)

        ## get jobs in running state
        r_jobs = filter(lambda j:j.jstat in ['E','R'], _jobs)
        for j in r_jobs:
            # job is counted to the leading host (i.e. the first node on the job property)
            g_job_count.labels(queue=_qcat(j.queue), status='running', host=j.node[0]).inc(1)
            
            # update core and memory usage
            m_chunk = 1.0 * j.rmem * 1000000000 / len(j.node)
            for n in j.node:
                g_core_usage.labels(queue=_qcat(j.queue), host=n).inc(1)
                g_mem_usage.labels(queue=_qcat(j.queue), host=n).inc(m_chunk)
                
        return

class ClusterEnergyConsumption(ClusterStatistics):
    """metrics collector for cluster energy consumption"""
    def __init__(self, config, lv=logging.ERROR):
        
        ClusterStatistics.__init__(self,config,lv)
        
        ## load config file and global settings
        c = getConfig(config)
        self.BIN_XYMONQ = c.get('MetricsPusher', 'BIN_XYMONQ')
        self.CFG_XYMONQ = c.get('MetricsPusher', 'CFG_XYMONQ')
        self.XYMON_PDU_LIST = c.get('MetricsPusher', 'XYMON_PDU_LIST').split(',')

    def collectMetrics(self):

        s = Shell(debug=False)

        g_energy_usage  = Gauge('hpc_energy_usage' , 'energy consumption watts per hour', ['pdu'], registry=self.registry)

        for pdu in self.XYMON_PDU_LIST:
            cmd = "%s -c %s -q xymondlog -H %s -T energy | grep 'DeviceStatusEnergy' | awk '{print $NF}'" % (self.BIN_XYMONQ, self.CFG_XYMONQ, pdu)
            rc, output, m = s.cmd1(cmd, allowed_exit=[0,255], timeout=300)
            if rc == 0:
                g_energy_usage.labels(pdu=pdu).set(float(output))
            else:
                logger.warn('Cannot retrieve energy consumption for %s', pdu)

        return
        
def testClusterMetrics(config):
    """Test function for MetricsRegistry"""
    m = ClusterStatistics(config, lv=logging.DEBUG)
    # collection metrics
    m.collectMetrics()
    # write out metrics to file
    m.exportToFile('statistics.prom')

    m = ClusterEnergyConsumption(config, lv=logging.DEBUG)
    m.collectMetrics()
    m.exportToFile('energy.prom')
    
    m = ClusterAccounting(config, lv=logging.DEBUG)
    m.collectMetrics()
    m.exportToFile('accounting.txt')
