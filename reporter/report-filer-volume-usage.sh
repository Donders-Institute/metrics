#!/bin/bash

[ -z $API_HOST ] && API_HOST="https://irulan-mgmt.dccn.nl"
[ -z $API_USER ] && API_USER="roadmin"

API_URL="${API_HOST}/api"

# curl command prefix
CURL="curl -k -#"

# Print usage message and document.
function usage() {
    echo "Usage: $1 <SVM> <VOLUME>" 1>&2
    cat << EOF >&2

This script reports NetApp ontap volume quota and usage using 
the ONTAP management APIs. It requires "curl" and "jq".

API documentation: https://library.netapp.com/ecmdocs/ECMLP2856304/html/index.html 

Environment variables:
            API_HOST: URL of the API host.
            API_USER: username for accessing the API server.
            API_PASS: password for accessing the API server (prompt for password if not set).
EOF
}

# Get the API href to the named object. 
function getHrefByQuery() {
    query=$1
    api_ns=$2
    ${CURL} -X GET -u ${API_USER}:${API_PASS} "${API_URL}/${api_ns}?${query}" | \
    jq '.records[] | ._links.self.href' | \
    sed 's/"//g'
}

# Get Object attributes by UUID in a generic way.
function getObjectByUUID() {

    uuid=$1
    api_ns=$2

    filter=".|.$(echo ${@:3} | sed 's/ /,./g')"

    ${CURL} -X GET -u ${API_USER}:${API_PASS} "${API_URL}/${api_ns}/${uuid}" | \
    jq ${filter}
}

# Get Object attributes by Href in a generic way.
function getObjectByHref() {
    href=$1
    filter=".|.$(echo ${@:2} | sed 's/ /,./g')"

    ${CURL} -X GET -u ${API_USER}:${API_PASS} "${API_HOST}/${href}" | \
    jq ${filter}
}

# Get Object attributes by name in a generic way.
function getObjectByName() {
    name=$1
    api_ns=$2

    href=$(getHrefByQuery "name=$name&svm=$API_SVM" $api_ns)

    [ "" == "$href" ] && echo "object not found: $name" >&2 && return 1
   
    getObjectByHref $href ${@:3}
}

# push metric to Prometheus push gateway
function pushMetric() {

    [ -z $URL_PUSH_GATEWAY ] && exit 1

    volume=$1
    size=$2
    used=$3

    cat << EOF | ${CURL} -X POST --data-binary @- "${URL_PUSH_GATEWAY}/metrics/job/filer_metrics"
# TYPE filer_volume_size gauge
# HELP filer_volume_size total volume size in bytes
filer_volume_size{volume="${volume}"} $size
# TYPE filer_volume_used gauge
# HELP filer_volume_used used volume size in bytes
filer_volume_used{volume="${volume}"} $used
EOF

}

## main program
[ $# -lt 2 ] && usage $0 && exit 1
API_SVM=$1
vol=$2

## prompt to ask for API_PASS if not set
[ -z $API_PASS ] &&
    echo -n "Password for API user ($API_USER): " &&  read -s API_PASS && echo

### For volume info.
echo "Getting volume $vol ..." &&
	sinfo=$(getObjectByName $vol "/storage/volumes" | jq '.space') || exit 1

size=$( echo $sinfo | jq '.size' )
used=$( echo $sinfo | jq '.used' )

pushMetric $vol $size $used || exit 1
