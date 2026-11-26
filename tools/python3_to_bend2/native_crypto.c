/* Native crypto primitive adapter. No network, JSON, SEH routes or policy.
 * A shell-free, bounded Process.run ABI: validate KEY_B64; nonce SIZE;
 * verify KEY_B64 SIGNATURE_B64URL (message on stdin). All failures nonzero.
 */
#include <openssl/evp.h>
#include <openssl/pem.h>
#include <openssl/rand.h>
#include <openssl/core_names.h>
#include <openssl/crypto.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

static int decode(const char *s, int url, unsigned char *out, size_t cap) {
    size_t n = strlen(s), pad = 0, raw = n;
    char buf[4096];
    if (!n || n > sizeof(buf)-5) return -1;
    while (raw && s[raw-1] == '=') raw--;
    if (n-raw > 2 || (!url && n%4) || (n!=raw && n%4) || raw%4==1) return -1;
    for (size_t i=0;i<raw;i++) {
        unsigned char c=s[i];
        if ((c>='A'&&c<='Z')||(c>='a'&&c<='z')||(c>='0'&&c<='9')) buf[i]=c;
        else if (url && c=='-') buf[i]='+';
        else if (url && c=='_') buf[i]='/';
        else if (!url && (c=='+'||c=='/')) buf[i]=c;
        else return -1;
    }
    pad=(4-raw%4)%4;
    if (n!=raw && n-raw!=pad) return -1;
    for (size_t i=0;i<pad;i++) buf[raw+i]='=';
    size_t len=raw+pad;
    if (len/4*3>cap) return -1;
    int got=EVP_DecodeBlock(out,(unsigned char*)buf,(int)len);
    return got<0 ? -1 : got-(int)pad;
}
static EVP_PKEY *load(const char *encoded) {
    unsigned char pem[3072];
    int n=decode(encoded,0,pem,sizeof(pem));
    if(n<=0 || n>2048 || memchr(pem,0,(size_t)n)) return NULL;
    for(int i=0;i<n;i++) if(pem[i]>127) return NULL;
    BIO *bio=BIO_new_mem_buf(pem,n);
    if(!bio) return NULL;
    EVP_PKEY *key=PEM_read_bio_PUBKEY(bio,NULL,NULL,NULL);
    BIO_free(bio);
    char group[80]; size_t size=0;
    if(!key || !EVP_PKEY_is_a(key,"EC") ||
       !EVP_PKEY_get_utf8_string_param(key,OSSL_PKEY_PARAM_GROUP_NAME,group,sizeof(group),&size) ||
       (strcmp(group,"prime256v1") && strcmp(group,"P-256"))) { EVP_PKEY_free(key); return NULL; }
    EVP_PKEY_CTX *ctx=EVP_PKEY_CTX_new_from_pkey(NULL,key,NULL);
    int valid=ctx && EVP_PKEY_public_check(ctx)==1;
    EVP_PKEY_CTX_free(ctx);
    if(!valid){EVP_PKEY_free(key);return NULL;}
    return key;
}
int main(int argc,char **argv) {
    if(argc==3 && !strcmp(argv[1],"nonce")) {
        char *end; long n=strtol(argv[2],&end,10);
        unsigned char bytes[64], text[90];
        if(*end || n<16 || n>64 || RAND_bytes(bytes,(int)n)!=1) return 1;
        int len=EVP_EncodeBlock(text,bytes,(int)n);
        for(int i=0;i<len;i++) {if(text[i]=='+') text[i]='-'; if(text[i]=='/') text[i]='_';}
        while(len && text[len-1]=='=') len--;
        return fwrite(text,1,(size_t)len,stdout)==(size_t)len ? 0:1;
    }
    if(argc==3 && !strcmp(argv[1],"validate")) {
        EVP_PKEY *key=load(argv[2]); if(!key)return 1; EVP_PKEY_free(key);return 0;
    }
    if(argc==4 && !strcmp(argv[1],"verify")) {
        unsigned char sig[192], message[8193];
        int len=decode(argv[3],1,sig,sizeof(sig));
        size_t n=fread(message,1,sizeof(message),stdin);
        if(len<=0 || len>128 || n>8192 || ferror(stdin)) return 1;
        EVP_PKEY *key=load(argv[2]); if(!key)return 1;
        EVP_MD_CTX *ctx=EVP_MD_CTX_new();
        int ok=ctx && EVP_DigestVerifyInit(ctx,NULL,EVP_sha256(),NULL,key)==1 &&
               EVP_DigestVerify(ctx,sig,(size_t)len,message,n)==1;
        EVP_MD_CTX_free(ctx);EVP_PKEY_free(key);
        return ok?0:1;
    }
    return 2;
}
