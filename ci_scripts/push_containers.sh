export IMAGE_TAG=$(cat VERSION)
export AARCH=`uname -m`
docker build -f Dockerfile.synchronizer -t cachengo/vtn-synchronizer-$AARCH:$IMAGE_TAG .
docker push cachengo/vtn-synchronizer-$AARCH:$IMAGE_TAG
