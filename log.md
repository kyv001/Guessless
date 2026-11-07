# 开发日志

## 基线1
> 15fe2fd2c808ca719a6bd89893c242a44e7e19cd

单纯的回溯搜索。

|configuration|mean|max|min|errs|timeouts|time per click|
|---|---|---|---|---|---|---|
|((9, 9), 10)|0.029s|0.125s|0.006s|0/10|0/10|0.001s/click|
|((16, 16), 40)|8.240s|25.023s|0.637s|0/10|1/10|0.113s/click|
|((30, 16), 99)|nans|nans|nans|0/10|10/10|nans/click|

## 优化1

