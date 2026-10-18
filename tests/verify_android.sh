#!/system/bin/sh
tr '\000' '\n' < /proc/$(pidof zygote64)/environ > /data/local/tmp/minis-verify-env
while IFS= read -r variable; do
    case "$variable" in
        ANDROID_*=*|BOOTCLASSPATH=*|DEX2OATBOOTCLASSPATH=*) export "$variable" ;;
    esac
done < /data/local/tmp/minis-verify-env
rm /data/local/tmp/minis-verify-env
exec /apex/com.android.art/bin/dex2oat64 --dex-file=/data/local/tmp/minis-fixed.apk --oat-file=/data/local/tmp/minis-verified.odex --compiler-filter=verify --instruction-set=x86_64 --runtime-arg -Xmx512m
