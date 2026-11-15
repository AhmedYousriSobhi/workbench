# The object store's endpoint and key pair, when the part is on (benchctl up --with objectstore): s5cmd ls s3://$WORKBENCH_S3_BUCKET
if [ -r /run/workbench/objectstore/s3.env ]; then
    set -a
    . /run/workbench/objectstore/s3.env
    set +a
fi
