FROM 172.16.50.89:5000/library/nginx:1.25.2-alpine
COPY nginx.conf /etc/nginx/nginx.conf
COPY site/ /srv/download/
USER 101
EXPOSE 8080
