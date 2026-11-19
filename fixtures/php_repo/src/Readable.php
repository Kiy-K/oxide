<?php

namespace App;

interface Readable extends \Countable
{
    public function read(string $key): string;
}
