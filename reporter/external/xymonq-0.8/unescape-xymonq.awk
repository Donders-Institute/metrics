#!/usr/bin/gawk -f

#!/usr/bin/mawk -f
## for busybox-test symlink awk first:
#!./awk -f

## Idea:
##	- we get the field-list by `-vFIELDS=$FIELDS` from xymonq
##	- each dataset is one line (as returned by `xymondboard`)
##	- all data is put into the data-hash, key is the field-name
##	- for the msg-field we have an unescape()-function
##	- output: we prefix each line with the hostname and print the
##	  (unescaped) msg in correct order

function unescape(msg) {
	#print "DEBUG: unescape starting: " msg;
	gsub("\\\\n", "\n", msg);
	gsub("\\\\t", "\t", msg);
	## do not replace CR as this scrambles output:
	#gsub("\\\\r", "\r", msg);
	gsub("\\\\p", "|", msg);
	sub("\\\\\\\\", "\\", msg);

	#print "DEBUG: unescape internal result: " msg;
	return(msg);
}

BEGIN {
	## xymondboard-output is "|"-separated:
	FS="|"
}


{
	## get our fieldnames and assign data to hash:
	split(FIELDS, fields_arr, ",");
	for (idx in fields_arr) {
#		printf("DEBUG: fields_arr[%s]=%s\n", idx, fields_arr[idx]);
		data[fields_arr[idx]] = $idx;
	}
	

#	## print all our fields and gathered data:
#	printf( "DEBUG: FIELDS=%s\n", FIELDS );
#	for ( idx in data) {
#		printf( "DEBUG: data[%s]=%s\n", idx, data[idx] );
#	}


	## print all other fields:
	for ( idx in data) {
		## do not print hostname:
		if ( idx == "hostname" ) 
			continue;

		## print msg line-by-line:
		if ( idx == "msg" )  {
			data["msg"] = unescape( data["msg"] );
			msg_length = split( data["msg"], msg, "\n" );
			for ( line = 1; line <= msg_length; line++ ) {
				if ( length( msg[line] ) == 0  ) {
					continue;
				}
				#printf( "DEBUG: length=%s\n", length( msg[line] ) );
				printf( "%s:%s:%s\n", data["hostname"], idx, msg[line] );
			}
			delete msg;
			continue;
		}

		## all others print as-is:
		printf( "%s:%s:%s\n", data["hostname"], idx, data[idx] );
		#printf( "DEBUG: data[%s]=%s\n", idx, data[idx] );
	}
}
