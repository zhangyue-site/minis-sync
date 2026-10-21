#include <jni.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/syscall.h>
#include <stdlib.h>
#include <string.h>

#ifndef RENAME_NOREPLACE
#define RENAME_NOREPLACE (1U << 0)
#endif

/* Filesystem paths use standard UTF-8, not JNI modified UTF-8 (emoji differ). */
static char *path_utf8(JNIEnv *env, jstring value) {
    jclass string_class = (*env)->FindClass(env, "java/lang/String");
    if (string_class == NULL) return NULL;
    jmethodID get_bytes = (*env)->GetMethodID(env, string_class, "getBytes", "(Ljava/lang/String;)[B");
    if (get_bytes == NULL) return NULL;
    jstring charset = (*env)->NewStringUTF(env, "UTF-8");
    if (charset == NULL) return NULL;
    jbyteArray bytes = (jbyteArray)(*env)->CallObjectMethod(env, value, get_bytes, charset);
    if ((*env)->ExceptionCheck(env) || bytes == NULL) return NULL;
    jsize length = (*env)->GetArrayLength(env, bytes);
    char *result = malloc((size_t)length + 1);
    if (result == NULL) return NULL;
    (*env)->GetByteArrayRegion(env, bytes, 0, length, (jbyte *)result);
    if ((*env)->ExceptionCheck(env)) { free(result); return NULL; }
    if (memchr(result, 0, (size_t)length) != NULL) { free(result); errno = EINVAL; return NULL; }
    result[length] = 0;
    return result;
}

/* Never fall back to rename(): its replacement semantics can destroy a concurrent edit. */
JNIEXPORT jint JNICALL
rename_file(JNIEnv *env, jclass cls, jstring source, jstring target, unsigned flags) {
    (void)cls;
    if (source == NULL || target == NULL) return EINVAL;
    errno = ENOMEM;
    char *from = path_utf8(env, source);
    if (from == NULL) return errno;
    errno = ENOMEM;
    char *to = path_utf8(env, target);
    if (to == NULL) { int error = errno; free(from); return error; }
    int error;
#ifdef __NR_renameat2
    int result = (int)syscall(__NR_renameat2, AT_FDCWD, from, AT_FDCWD, to,
                              flags);
    error = result == 0 ? 0 : errno;
#else
    error = ENOSYS;
#endif
    free(to);
    free(from);
    return error;
}

JNIEXPORT jint JNICALL Java_site_zhangyue_minis_sync_NativeFiles_publish(JNIEnv *env, jclass cls, jstring source, jstring target) {
    return rename_file(env, cls, source, target, RENAME_NOREPLACE);
}
JNIEXPORT jint JNICALL Java_site_zhangyue_minis_sync_NativeFiles_exchange(JNIEnv *env, jclass cls, jstring source, jstring target) {
    return rename_file(env, cls, source, target, 2U); /* RENAME_EXCHANGE */
}
