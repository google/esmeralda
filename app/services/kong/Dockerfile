# Dockerfile for Kong Gateway serverless deployment in Google Cloud Run v2
FROM kong:3.7-ubuntu

USER root
RUN mkdir -p /etc/kong && chown -R kong:kong /etc/kong
USER kong

COPY kong.conf.default /etc/kong/kong.conf
EXPOSE 8000
CMD ["kong", "docker-start"]
