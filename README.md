# Video to Frames API

Деплой на Render: залей папку в GitHub-репозиторий -> New + -> Web Service -> Runtime: Docker.
Опционально задай переменную окружения API_KEY.

Пример запроса:
curl -X POST "https://YOUR-APP.onrender.com/extract?fps=2&width=640&fmt=jpg" \
  -H "X-API-Key: ВАШ_КЛЮЧ" -F "video=@clip.mp4" -o frames.zip

## Звук
Только звук:
curl -X POST "https://YOUR-APP.onrender.com/audio?bitrate=128k" -H "X-API-Key: КЛЮЧ" -F "video=@clip.mp4" -o audio.mp3

Кадры + звук в одном архиве (внутри будет audio.mp3):
curl -X POST "https://YOUR-APP.onrender.com/extract?fps=2&include_audio=true" -H "X-API-Key: КЛЮЧ" -F "video=@clip.mp4" -o frames.zip

## Видео по ссылке (вместо загрузки файла)
curl -X POST "https://YOUR-APP.onrender.com/extract?fps=2&url=https://raw.githubusercontent.com/USER/REPO/main/clip.mp4" -H "X-API-Key: КЛЮЧ" -o frames.zip
