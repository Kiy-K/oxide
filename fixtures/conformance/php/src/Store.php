<?php

namespace App\Store;

use App\Support\Logger;
include 'legacy.php';

final class Store extends Base implements Readable, \JsonSerializable
{
    use Cacheable;

    public const VERSION = '1.0';

    private string $name;

    public function __construct(string $name)
    {
        $this->name = $name;
    }

    public function find(string $key): string
    {
        Logger::write($key);
        return $this->data[$key] ?? '';
    }

    #[\Override]
    public function read(string $key): string
    {
        return $this->find($key);
    }

    public static function create(string $name): self
    {
        return new self($name);
    }
}

function build(): Store
{
    return new Store('x');
}
