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
> 9d0184abd2941f601bbf34b90f9cc3a4da3b78b9

使用set而不是list，在回溯前进行传播。

|configuration|mean|max|min|errs|timeouts|time per click|
|---|---|---|---|---|---|---|
|((9, 9), 10)|0.219s|2.045s|0.006s|0/10|0/10|0.011s/click|
|((16, 16), 40)|7.660s|32.707s|0.268s|0/10|4/10|0.096s/click|
|((30, 16), 99)|nans|nans|nans|0/10|10/10|nans/click|

性能似乎不升反降。

## 优化二

