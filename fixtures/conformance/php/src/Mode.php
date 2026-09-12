<?php

namespace App\Store;

enum Mode: string implements Readable
{
    case Fast = 'fast';
    case Slow = 'slow';

    public function read(string $key): string
    {
        return ucfirst($this->value);
    }
}
