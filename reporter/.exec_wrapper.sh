#!/bin/bash

## This wrapper script makes sure Environment module is loaded,
## and lanuch a corresponding python executable.

#---------------------------------------------------------------------#
# get_script_dir: resolve absolute directory in which the current     #
#                 script is located.                                  #
#---------------------------------------------------------------------#
function get_script_dir() {
    ## resolve the base directory of this executable
    local SOURCE=$1
    while [ -h "$SOURCE" ]; do
        # resolve $SOURCE until the file is no longer a symlink
        DIR="$( cd -P "$( dirname "$SOURCE" )" && pwd )"
        SOURCE="$(readlink "$SOURCE")"

        # if $SOURCE was a relative symlink,
        # we need to resolve it relative to the path
        # where the symlink file was located

        [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
    done

    echo "$( cd -P "$( dirname "$SOURCE" )" && pwd )"
}

me=`basename $0`

flock=/tmp/.${me}.lock

if [ -f $flock ]; then
    echo "previous process is still running ... exiting"
    exit 0
fi

touch $flock

dir=$( get_script_dir $0 )

## checking whether the cluster environment module is loaded
module list | grep cluster > /dev/null 2>&1
if [ $? != 0 ]; then
    # take care of different mount points
    for d in "/opt" "/mnt/software"; do
        if [ -f ${d}/_modules/setup.sh ]; then
            source ${d}/_modules/setup.sh
        fi
    done
    module load cluster
fi

myexec=$(echo $me | sed 's/.sh/.py/')

## run the executable passing command-line arguments
${dir}/${myexec} "$@"

rm -f $flock
