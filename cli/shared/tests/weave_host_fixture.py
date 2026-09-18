"""Native fixture generator. No product implementation is imported."""
import hashlib
from pathlib import Path
import shutil
import subprocess

SOURCE = r'''
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <string.h>
#include <signal.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <limits.h>
extern char **environ;
static void quoted(FILE *f,const char *s){fputc('"',f);for(;*s;s++){unsigned char c=*s;if(c=='"'||c=='\\')fprintf(f,"\\%c",c);else if(c<32||c>=127)fprintf(f,"\\u%04x",c);else fputc(c,f);}fputc('"',f);}
static void pause_ms(long ms){struct timespec t={ms/1000,(ms%1000)*1000000};nanosleep(&t,0);}
static void marker(const char *p,const char *v){FILE*f=fopen(p,"w");if(!f)_exit(91);fputs(v,f);fclose(f);}
static void term(int n){(void)n;marker("descendant-signalled","signal");}
static void hexout(int fd,char *s){unsigned int b;for(size_t i=0;s[i]&&s[i]!='\n';i+=2){if(sscanf(s+i,"%2x",&b)!=1)_exit(92);unsigned char c=b; if(write(fd,&c,1)<0)_exit(93);}}
int main(int argc,char **argv){
 char planpath[PATH_MAX];snprintf(planpath,sizeof(planpath),"%s.plan",argv[0]);FILE*p=fopen(planpath,"r");if(!p)return 90;
 char behavior[100],out[131074],err[131074];long delay=0;int code=0;
 if(!fgets(behavior,sizeof(behavior),p)||!fgets(out,sizeof(out),p)||!fgets(err,sizeof(err),p)||fscanf(p,"%ld\n%d",&delay,&code)!=2)return 90;fclose(p);behavior[strcspn(behavior,"\n")]=0;
 FILE*f=fopen("observation.json","w");if(!f)return 91;
 fprintf(f,"{\"pid\":%ld,\"argv\":[",(long)getpid());for(int i=1;i<argc;i++){if(i>1)fputc(',',f);quoted(f,argv[i]);}fputs("],\"environment\":{",f);
 for(int i=0;environ[i];i++){if(i)fputc(',',f);char*s=strchr(environ[i],'=');if(!s)return 92;*s=0;quoted(f,environ[i]);*s='=';fputc(':',f);quoted(f,s+1);}
 char cwd[PATH_MAX];getcwd(cwd,sizeof(cwd));fputs("},\"cwd\":",f);quoted(f,cwd);char ch;int eof=read(0,&ch,1)==0;fprintf(f,",\"stdinEOF\":%s}\n",eof?"true":"false");fclose(f);
 if(!strcmp(behavior,"ignore-term-and-sleep"))signal(SIGTERM,SIG_IGN);
 if(!strcmp(behavior,"write-pending-then-wait")){marker("pending-file","pending-effect\n");for(;;)pause();}
 if(!strcmp(behavior,"exit-zero-leave-descendant-pipe-open")){
   int gate[2];pipe(gate);pid_t child=fork();if(child==0){close(gate[0]);signal(SIGTERM,term);signal(SIGINT,term);FILE*d=fopen("descendant.pid","w");fprintf(d,"%ld",(long)getpid());fclose(d);write(gate[1],"r",1);close(gate[1]);pause_ms(8000);_exit(0);}close(gate[1]);read(gate[0],&ch,1);close(gate[0]);return 0;
 }
 if(!strcmp(behavior,"concurrent-output-flood")){pid_t child=fork();if(child>0){FILE*d=fopen("descendant.pid","w");fprintf(d,"%ld",(long)child);fclose(d);}int fd=child==0?2:1;char b[4096];memset(b,fd==1?'o':'e',sizeof(b));for(;;)if(write(fd,b,sizeof(b))<0)_exit(0);}
 if(!strcmp(behavior,"saturate-stdout-then-sleep")||!strcmp(behavior,"saturate-stderr-then-sleep")){int fd=!strcmp(behavior,"saturate-stdout-then-sleep")?1:2;char b[4096];memset(b,fd==1?'o':'e',sizeof(b));for(int i=0;i<256;i++)if(write(fd,b,sizeof(b))<0)_exit(0);pause_ms(5000);return 0;}
 if(!strcmp(behavior,"write-until-stopped")){char b[4096];memset(b,'x',sizeof(b));for(;;)if(write(1,b,sizeof(b))<0)_exit(0);}
 hexout(1,out);hexout(2,err);pause_ms(delay);return code;
}
'''


def build_host(root, cc):
    source = root / 'fake-host.c'
    binary = root / 'fixture-host'
    source.write_text(SOURCE)
    command = [str(cc), '-std=c99', '-O0', str(source), '-o', str(binary)]
    result = subprocess.run(command, env={}, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError('fixture compiler failed: ' + result.stderr.decode(errors='replace')[:2000])
    return binary, command


def expand(value, tokens):
    if isinstance(value, str):
        for key, replacement in tokens.items():
            value = value.replace(key, replacement)
        return value
    if isinstance(value, list):
        return [expand(v, tokens) for v in value]
    if isinstance(value, dict):
        return {k: expand(v, tokens) for k, v in value.items()}
    return value


def construct(root, native_host, document, case):
    import json
    import os
    host = root / 'fake-host'
    shutil.copyfile(native_host, host)
    host.chmod(0o700)
    behavior = case.get('host', {})
    unknown = set(behavior) - {'behavior', 'stdoutHex', 'stderrHex', 'sleepMs', 'exitCode', 'expectedEnvironment'}
    if unknown: raise ValueError('unsupported host fields: ' + repr(sorted(unknown)))
    if behavior.get('behavior', 'normal') not in {'normal', 'assert-environment', 'executable-with-missing-interpreter', 'ignore-term-and-sleep', 'write-pending-then-wait', 'exit-zero-leave-descendant-pipe-open', 'concurrent-output-flood', 'write-until-stopped', 'saturate-stdout-then-sleep', 'saturate-stderr-then-sleep'}:
        raise ValueError('unsupported host behavior')
    if behavior.get('behavior') == 'executable-with-missing-interpreter':
        host.write_bytes(b'#!/no-such-weave-fixture-interpreter\n')
    (root / 'fake-host.plan').write_text('\n'.join([
        behavior.get('behavior', 'normal'), behavior.get('stdoutHex', ''),
        behavior.get('stderrHex', ''), str(behavior.get('sleepMs', 0)),
        str(behavior.get('exitCode', 0)), '']))
    tokens = {'{{ROOT}}': str(root), '{{HOST}}': str(host),
              '{{CONFIG}}': str(root / 'weave.json'), '{{BINDING}}': str(root / 'host.json'),
              '{{HOST_SHA256}}': hashlib.sha256(host.read_bytes()).hexdigest()}
    selected = expand(case, tokens)
    binding = expand(document['harness']['baseBinding'], tokens)
    binding.update(selected.get('bindingPatch', {}))
    path = root / 'host.json'
    raw = selected.get('bindingRawUtf8', json.dumps(binding))
    path.write_bytes(raw.encode())
    mutation = selected.get('bindingFile')
    if mutation in ('missing', 'fifo', 'directory'):
        path.unlink()
        if mutation == 'fifo': os.mkfifo(path)
        if mutation == 'directory': path.mkdir()
    elif mutation == 'oversized-65537': path.write_bytes(b' ' * 65537)
    elif mutation == 'invalid-utf8': path.write_bytes(b'\xff')
    elif mutation == 'utf8-bom': path.write_bytes(b'\xef\xbb\xbf' + path.read_bytes())
    elif mutation is not None: raise ValueError('unsupported bindingFile: ' + mutation)
    mutation = selected.get('hostFile')
    if mutation in ('missing', 'directory'):
        host.unlink()
        if mutation == 'directory': host.mkdir()
    elif mutation == 'not-executable': host.chmod(0o600)
    elif mutation == 'changed-after-binding':
        with host.open('ab') as stream: stream.write(b'changed')
    elif mutation == 'oversized-536870913':
        with host.open('r+b') as stream: stream.truncate(536870913)
    elif mutation is not None: raise ValueError('unsupported hostFile: ' + mutation)
    return selected
