FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
# CPU-only torch 先装:sentence-transformers 的 torch 依赖无版本约束(uv.lock {name="torch"} 无 specifier),
# pip 见已装 torch==2.12.1+cpu 满足即跳过,不重拉 CUDA 版(省 ~4GB:nvidia 2.7G + triton 691M + cuda-toolkit 等)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -e "."
COPY src/ src/
EXPOSE 8001
CMD ["uvicorn", "src.engine:app", "--host", "0.0.0.0", "--port", "8001"]
