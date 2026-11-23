# cite-analyser service image (lens family contract: serve on its assigned port)
FROM python:3.13-slim

RUN pip install --no-cache-dir "cite-analyser==0.2.2"

EXPOSE 8006
ENV PYTHONUNBUFFERED=1
CMD ["cite-analyser", "serve", "--host", "0.0.0.0", "--port", "8006"]
