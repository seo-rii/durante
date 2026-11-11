"""Stock interpreter/JIT arithmetic with a fixed functional result."""
value = 0
for index in range(50000):
    value += index
print(value)
