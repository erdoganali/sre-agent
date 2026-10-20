KUBE_EDITOR="perl -ni -e 'print unless /^  message: /'" kc -n "$LAB_NS" edit configmap sre-lab-app >/dev/null
kc -n "$LAB_NS" rollout restart deploy/sre-lab-app >/dev/null
