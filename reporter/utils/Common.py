#/usr/bin/env python
from email.mime.text import MIMEText
from subprocess import Popen, PIPE

import logging
import inspect
import time
import datetime 
import re 
import math 
import locale
import ConfigParser
import StringIO
import gzip
import pycurl

import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__))+'/../external/lib/python')

## loading MySQL library for updating the project database
try:
    ## using pure python MySQL client
    import MySQLdb as mdb
except Exception, e:
    ## trying mysql.connector that requires MySQL client library 
    import mysql.connector as mdb
    from mysql.connector import errorcode

def getMySQLConnector(host,uid,passwd,db):
    ''' establishes MySQL connector
    '''
    cnx    = None
    config = None 

    if mdb.__name__ == 'MySQLdb':
        ### use MySQLdb library
        config = {'user'   : uid,
                  'passwd' : passwd,
                  'db'     : db,
                  'host'   : host }
        try:
            cnx = mdb.connect(**config)
        except mdb.Error, e:
            logging.error('db query error %d: %s' % (e.args[0],e.args[1]))

            if cnx: cnx.close()
    else:
        ### use mysql-connector library
        config = {'user'             : uid,
                  'password'         : passwd,
                  'database'         : db,
                  'host'             : host,
                  'raise_on_warnings': True }
        try:
            cnx = mdb.connect(**config)
        except mdb.Error, err:
            if err.errno == errorcode.ER_ACCESS_DENIED_ERROR:
                logging.error("something is wrong with your user name or password")
            elif err.errno == errorcode.ER_BAD_DB_ERROR:
                logging.error("database does not exists")
            else:
                logging.error(err)

            if cnx: cnx.close()

    return cnx

# a class for curl callback 
class CURLCallback:
    def __init__(self):
        self.header   = ''
        self.contents = ''

    def header_callback(self, buf):
        self.header = self.header + buf

    def body_callback(self, buf):
        self.contents = self.contents + buf

    def progress_callback(self, download_t, download_d, upload_t, upload_d):
        logging.info('uploaded %d:%d', upload_d, upload_t)

def pushMetric(url, m):
    '''POST metric data'''

    t = CURLCallback()
    c = pycurl.Curl()
    c.setopt(c.URL, url)
    c.setopt(c.CONNECTTIMEOUT, 3)
    c.setopt(c.TIMEOUT, 10)
    c.setopt(c.HEADERFUNCTION, t.header_callback)
    c.setopt(c.WRITEFUNCTION , t.body_callback)
    c.setopt(c.CUSTOMREQUEST , 'POST')
    c.setopt(c.POSTFIELDSIZE , len(m))
    c.setopt(c.POSTFIELDS    , m)
    c.perform()
    code = c.getinfo(pycurl.HTTP_CODE)
    c.close()

    if code >= 400:
        logging.error('%s', t.header)
        logging.error('%s', t.contents)

# a class make the dictionary hashable 
class HashableDict(dict):
    def __hash__(self):
        return hash(tuple(sorted(self.items())))

def gzipContent(content):
    out = StringIO.StringIO()
    f = gzip.GzipFile(fileobj=out, mode='w', compresslevel=5)
    f.write(content)
    f.close()
    return out.getvalue()

def getMyLogger(name=None):

    if name is None:
        name = inspect.stack()[1][3]

    logging.basicConfig(format='[%(levelname)s:%(name)s] %(message)s', level=logging.WARNING)
    return logging.getLogger(name)

def getConfig(config_file='config.ini'):
    '''
    read and parse the config.ini file
    '''

    default_cfg = {
        # OpenTSDB config
        'URL_PUSH' : '',
        # Torque configuration 
        'DB_DATA_DIR'        : '/var/log/torque/torquemon_db',
        'TORQUE_LOG_DIR'     : '/home/common/torque/job_logs',
        'TORQUE_BATCH_QUEUES': 'short,medium,long',
        'BIN_QSTAT_ALL'      : 'hpcutil cluster qstat',
        'BIN_FSHARE_ALL'     : '',
        'BIN_CLUSTER_MATLAB' : 'hpcutil cluster matlablic',
        'NOTIFICATION_EMAILS': '',
        # Project database interface
        'PDB_USER'         : '',
        'PDB_PASSWORD'     : '',
        'PDB_HOST'         : '',
        'PDB_DATABASE'     : '',
        # TSDB endpoints
        'OPENTSDB_HOST'      : 'opentsdb',
        'OPENTSDB_PORT'      : '9042',
        'PROMETHEUS_GW_HOST' : 'gw-prometheus',
        'PROMETHEUS_GW_PORT' : '9091'
    }

    config = ConfigParser.SafeConfigParser(default_cfg)
    config.read(config_file)

    return config

def getTimeInvolvement(mode='year', tdelta='7d', tstart=datetime.datetime.now()):
    '''resolve the years/months/days involved given the time range [tstart, tstart+tdelta]
       it returns an array of time string, for example:
         - ['2013','2014','2015']  if mode is 'year' and the time range involves years of 2013-2015
         - ['201401','201402']     if mode is 'month' and the time range involves months of Jan.-Feb. in 2014
         - ['20140101','20140102'] if mode is 'day' and the time range involves 1 Jan. and 2 Jan. of 2014
    '''

    re_td = re.compile('^([\-,\+]?[0-9\.]+)\s?(y|Y|m|M|d|D){0,1}$')  ## only recognize certain pattern of tdelta argument 

    ## default is 7 days in difference
    t_diff = 7
    t_unit = 'd'
    m = re_td.match(tdelta)
    if m:
        t_diff = float(m.group(1))
        if m.group(2):           ## override the default unit of 'd' if it's given in tdelta
            t_unit = m.group(2)

    ## convert to days, assuming
    ##  - 1 y = 365 d
    ##  - 1 m = 30 d
    if t_unit in ['y','Y']:
        t_diff = t_diff*365
    elif t_unit in ['m','M']:
        t_diff = t_diff*30 
    else:
        pass

    r_beg = int(math.floor(t_diff))
    r_end = 0
    if t_diff > 0:
        r_beg = 1 
        r_end = int(math.ceil(t_diff)) + 1 

    ts_digits = {'year':4, 'month':6, 'day':8}

    days = []
    days.append( tstart.strftime('%Y%m%d')[0:ts_digits[mode]] )
    for dt in range(r_beg, r_end):
        tnew = tstart + datetime.timedelta(days=dt)
        days.append( tnew.strftime('%Y%m%d')[0:ts_digits[mode]] )

    return sorted( list(set(days)) )

def parseTimeStringLocale(timeString):
    '''
    parse locale-dependent time string with timezone into seconds since epoch 
    '''

    t = None

    logger = getMyLogger()

    ## try parsing the time string with different locale
    for l in ['en_US', 'nl_NL']:
        locale.setlocale(locale.LC_TIME, l)
        try:
            t = time.strptime(timeString, '%a %b %d %H:%M:%S %Y %Z')
        except ValueError,e:
            try:
                t = time.strptime(timeString, '%a %b %d %H:%M:%S %Z %Y')
            except ValueError,e:
                pass
        if t:
            break

    ## set back to default locale
    locale.setlocale(locale.LC_TIME, '')

    return time.mktime(t)

def makeStructTimeUTC(value):
    '''
    Convert given time value into proper timestamp in UTC
    '''
    utc_tt = None
    if type(value) in [int,float,long]: ## value is second from epoch
        utc_tt = time.gmtime(value)
    elif type(value) == str: ## value is a string with timezone
        utc_tt = time.gmtime( parseTimeStringLocale(value) )
        if not utc_tt:
            raise ValueError('cannot parse time string: %s' % value)
    elif value == None:
        pass
    else:
        utc_tt = value

    return utc_tt

def fmtStructTimeUTC(stime):
    '''
    Format given struct time in to human readable string
    ''' 
    return time.strftime('%a %b %d %H:%M:%S %Y',stime)

def sendEmailNotification(from_email, to_emails, subject, msg):
    """
    sends email notification via sendmail daemon on local machine.
    :param from_email: the FROM email address
    :param to_emails: a list of email addresses to send the message
    :param subject: subject of the message
    :param msg: the message body in plain text
    :return: (stdoutdata, stderrdata) of the sendmail command
    """

    c = MIMEText(msg)
    c["From"] = from_email
    c["To"] = ",".join(to_emails)
    c["Subject"] = subject
    p = Popen(["/usr/sbin/sendmail", "-t", "-oi"], stdin=PIPE)
    return p.communicate(c.as_string())
