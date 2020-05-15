#!/bin/bash

[ -z $API_HOST ] && API_HOST="https://irulan-mgmt.dccn.nl"
[ -z $API_USER ] && API_USER="roadmin"
[ -z $API_SVM  ] && API_SVM="fremem"

API_URL="${API_HOST}/api"

# curl command prefix
CURL="curl -k -#"

# Print usage message and document.
function usage() {
    echo "Usage: $1 [<SVM>:]<VOLUME> ..." 1>&2
    cat << EOF >&2

This script reports NetApp ontap volume quota and usage using 
the ONTAP management APIs. It requires "curl" and "jq".

API documentation: https://library.netapp.com/ecmdocs/ECMLP2856304/html/index.html 

Environment variables:
            API_HOST: URL of the API host.
            API_SVM:  default vserver name.
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
    svm=$2
    api_ns=$3

    echo $name $svm $api_ns >> test.out

    href=$(getHrefByQuery "name=$name&svm=$svm" $api_ns)

    [ "" == "$href" ] && echo "object not found: $name" >&2 && return 1
   
    getObjectByHref $href ${@:4}
}

# make metric to OpenTSDB 
function makeMetricTSDB() {
    ts=$(date +%s)
    m="[]"
    for d in $@; do
	volume=$(echo $d | awk -F ':' '{print $1}')
	size=$(echo $d | awk -F ':' '{print $2}')
	used=$(echo $d | awk -F ':' '{print $3}')


        m=$(echo $m | jq --arg v "$volume" \
                         --arg s "$size" \
			 --arg t "$ts" \
                         -c -M \
			 '.+=[{"timestamp":($t|tonumber), "metric": "filer.volume.size", "value":($s|tonumber), "tag":{"volume":$v}}]' | \
                      jq --arg v "$volume" \
                         --arg s "$used" \
			 --arg t "$ts" \
                         -c -M \
			 '.+=[{"timestamp":($t|tonumber), "metric": "filer.volume.used", "value":($s|tonumber), "tag":{"volume":$v}}]' )
    done
    echo $m
}

function pushMetricTSDB() {
    [ -z $URL_TSDB ] && return 1
    makeMetricTSDB $@ | ${CURL} -X POST --data-binary @- "${URL_TSDB}/api/put"
}

# make metric to Prometheus push gateway
function makeMetricPrometheus() {

    echo "# TYPE filer_volume_size gauge"
    echo "# HELP filer_volume_size total volume size in bytes"
    for d in $@; do
	volume=$(echo $d | awk -F ':' '{print $1}')
	size=$(echo $d | awk -F ':' '{print $2}')
        echo "filer_volume_size{volume=\"${volume}\"} $size"
    done

    echo "# TYPE filer_volume_used gauge"
    echo "# HELP filer_volume_used used volume size in bytes"
    for d in $@; do
	volume=$(echo $d | awk -F ':' '{print $1}')
	used=$(echo $d | awk -F ':' '{print $3}')
        echo "filer_volume_used{volume=\"${volume}\"} $used"
    done
}

function pushMetricPrometheus() {
    [ -z $URL_PUSH_GATEWAY ] && return 1
    makeMetricPrometheus $@ | ${CURL} -X POST --data-binary @- "${URL_PUSH_GATEWAY}/metrics/job/filer_metrics"
}

## main program
[ $# -lt 1 ] && usage $0 && exit 1

## prompt to ask for API_PASS if not set
[ -z $API_PASS ] &&
    echo -n "Password for API user ($API_USER): " &&  read -s API_PASS && echo

data=()
for v in $@; do
    svm=$API_SVM
    vol=$( echo $v | awk -F ':' '{print $NF}' )
    [ $( echo $v | awk -F ':' '{print NF}' ) -eq 2 ] &&
	    svm=$( echo $v | awk -F ':' '{print $1}' )

    ### For volume info.
    echo "Getting volume $vol, vserver $svm ..." &&
        sinfo=$(getObjectByName $vol $svm "/storage/volumes" | jq '.space') || exit 1
    size=$( echo $sinfo | jq '.size' )
    used=$( echo $sinfo | jq '.used' )
    data+=( $vol:$size:$used )
done

pushMetricPrometheus ${data[@]} || exit 1
#makeMetricTSDB ${data[@]} | jq
