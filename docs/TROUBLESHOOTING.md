# Диагностика

## Jira отклонила token (401)

Создайте новый PAT по правилам вашей Jira, проверьте отсутствие пробелов и VPN. Не отправляйте token в чат/скриншот/лог. 403 означает недостаток прав; 404 может означать отсутствие записи или прав видеть её.

## TLS certificate

Не отключайте verification. Получите PEM CA bundle у IT, положите в `certs/` и в UI укажите `/certs/<имя>.pem`. Ошибка `JIRA_TLS` обычно означает неправильный bundle/цепочку или TLS interception proxy.

## Proxy/VPN

Proxy вводится в расширенных настройках подключения. Проверьте, что proxy разрешает Jira и не заменяет сертификат без доверенного CA. Model container в интернет ходить не должен.

## Модель долго запускается / мало RAM

Первый health может ждать до трёх минут. Закройте тяжёлые приложения, оставьте `LLAMA_GPU_LAYERS=0`, проверьте 32 ГБ RAM. Если memory pressure остаётся, используйте Qwen3-4B Q4_K_M после обновления model/checksum/config и повторного quality benchmark.

## Model runtime unhealthy

Проверьте `docker compose ps` и `docker compose logs model --tail 100`, наличие `models/Qwen3-4B-Q4_K_M.gguf`, checksum `cd models && shasum -a 256 -c SHA256SUMS`. Runtime port намеренно не доступен с host.

## Windows/WSL2

Убедитесь, что проект находится в доступном Docker Desktop каталоге и WSL integration включена. CPU fallback работает; CUDA требует совместимые NVIDIA driver, WSL2 и Container Toolkit — default Compose этого не обещает.

## Сценарий отклонён валидатором

Приложение уже сделало одну repair-попытку. Повторите один раз с меньшим контекстом. Если diagnostic code сохраняется, запишите только code/correlation id и версию model/runtime; не прикладывайте комментарии Jira или prompt.

