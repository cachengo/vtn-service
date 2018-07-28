export IMAGE_TAG=$(cat VERSION)

docker manifest create --amend cachengo/vtn-synchronizer:$IMAGE_TAG cachengo/vtn-synchronizer-x86_64:$IMAGE_TAG cachengo/vtn-synchronizer-aarch64:$IMAGE_TAG

docker manifest push cachengo/vtn-synchronizer:$IMAGE_TAG