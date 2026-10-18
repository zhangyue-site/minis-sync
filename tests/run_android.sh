#!/system/bin/sh
tr '\000' '\n' < /proc/$(pidof zygote64)/environ > /data/local/tmp/minis-test-env
while IFS= read -r variable; do
    case "$variable" in
        ANDROID_*=*|BOOTCLASSPATH=*|DEX2OATBOOTCLASSPATH=*) export "$variable" ;;
    esac
done < /data/local/tmp/minis-test-env
rm /data/local/tmp/minis-test-env
export CLASSPATH=/data/local/tmp/acceptance.dex:/data/local/tmp/minis-fixed.apk
exec app_process /system/bin test.Acceptance "$@"
